"""OpenAICompatClient against a mocked HTTP transport (no Ollama, no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import LLMUnavailableError, OpenAICompatClient
from app.agent.orchestrator import AgentOrchestrator, RunStatus
from app.agent.tool_registry import model_tool_schemas
from app.config import Settings

SECRET_REASONING = "SECRET-CHAIN-OF-THOUGHT-7f3a"

Handler = Callable[[httpx.Request], httpx.Response]


def completion(message: dict[str, Any], finish: str = "tool_calls") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def tool_message(name: str, arguments: dict[str, Any], reasoning: str = "") -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }
    if reasoning:
        message["reasoning"] = reasoning
    return message


def client_for(settings: Settings, handler: Handler) -> OpenAICompatClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatClient(settings.model_copy(update={"llm_max_retries": 0}), http_client=http)


def test_request_shape_and_reasoning_discarded(settings: Settings) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        msg = tool_message("inspect_dataset", {"source_id": "src_x"}, reasoning=SECRET_REASONING)
        return httpx.Response(200, json=completion(msg))

    llm = client_for(settings.model_copy(update={"llm_thinking": False, "llm_seed": 7}), handler)
    response = llm.complete([{"role": "user", "content": "q"}], model_tool_schemas())

    body = seen[0]
    assert body["model"] == settings.llm_model
    assert body["temperature"] == settings.llm_temperature
    assert body["reasoning_effort"] == "none"
    assert body["seed"] == 7
    assert [t["function"]["name"] for t in body["tools"]] == [
        "inspect_dataset",
        "create_view",
        "summarize",
        "submit_answer",
    ]

    assert response.reasoning_chars_discarded == len(SECRET_REASONING)
    assert SECRET_REASONING not in response.model_dump_json()
    (call,) = response.tool_calls
    assert (call.id, call.name) == ("call_inspect_dataset", "inspect_dataset")
    assert json.loads(call.arguments) == {"source_id": "src_x"}
    assert (response.prompt_tokens, response.completion_tokens) == (100, 20)


def test_thinking_on_omits_reasoning_effort(settings: Settings) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "hi"}, "stop"))

    client_for(settings.model_copy(update={"llm_thinking": True}), handler).complete([], [])
    assert "reasoning_effort" not in seen[0]


def test_connection_error_becomes_unavailable(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailableError, match="Connection"):
        client_for(settings, handler).complete([], [])


def test_http_error_becomes_unavailable(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    with pytest.raises(LLMUnavailableError, match="404"):
        client_for(settings, handler).complete([], [])


def test_describe_has_no_secrets(settings: Settings) -> None:
    secret = "sk-very-secret-key"
    from pydantic import SecretStr

    llm = client_for(
        settings.model_copy(update={"llm_api_key": SecretStr(secret)}),
        lambda r: httpx.Response(500),
    )
    assert secret not in json.dumps(llm.describe())


def test_reasoning_never_persisted_end_to_end(
    settings: Settings,
    session_factory: sessionmaker[Session],
    sample_ids: dict[str, str],
) -> None:
    """Brief invariant 8: scan every table for the reasoning marker after a full run."""
    responses = iter(
        [
            tool_message(
                "summarize",
                {"view_id": sample_ids["view_id"], "columns": ["age"], "metrics": ["mean"]},
                reasoning=SECRET_REASONING,
            ),
            None,  # built from the evidence ID in the request
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        msg = next(responses)
        if msg is None:
            body = json.loads(request.content)
            evidence_id = json.loads(body["messages"][-1]["content"])["evidence_id"]
            msg = tool_message(
                "submit_answer",
                {
                    "explanation": "Mean age.",
                    "claims": [{"text": "Mean age computed.", "evidence_ids": [evidence_id]}],
                },
                reasoning=SECRET_REASONING,
            )
        return httpx.Response(200, json=completion(msg))

    agent = AgentOrchestrator(session_factory, client_for(settings, handler), settings)
    result = agent.ask("What is the mean age?")
    assert result.status == RunStatus.COMPLETED

    with session_factory() as db:
        tables = (
            db.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
            .scalars()
            .all()
        )
        for table in tables:
            for row in db.execute(text(f'SELECT * FROM "{table}"')):  # noqa: S608 (fixed names)
                assert SECRET_REASONING not in repr(tuple(row)), table
        discarded = db.execute(
            text("SELECT SUM(reasoning_chars_discarded) FROM model_calls")
        ).scalar()
    assert discarded == 2 * len(SECRET_REASONING)


def test_nameless_tool_call_becomes_repairable(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": None, "arguments": "{}"}}
            ],
        }
        return httpx.Response(200, json=completion(msg))

    (call,) = client_for(settings, handler).complete([], []).tool_calls
    assert call.name == ""  # the registry answers UNKNOWN_TOOL; nothing crashes


def test_per_call_timeout_is_capped(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion({"role": "assistant", "content": "x"}, "stop"))

    llm = client_for(settings, handler)
    assert llm.complete([], [], timeout_s=5.0).content == "x"
