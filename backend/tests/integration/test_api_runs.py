"""M3b HTTP API: background runs with polling, provenance endpoints, guards, restart."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import (
    FakeLLMClient,
    LLMUnavailableError,
    ModelResponse,
    ScriptStep,
    tool_call,
)
from app.agent.orchestrator import AgentOrchestrator
from app.config import Settings
from app.db.session import create_app_engine
from app.main import create_app

API = "/api/v1"
Messages = Sequence[dict[str, Any]]


def calls(*tc: Any) -> ModelResponse:
    return ModelResponse(tool_calls=list(tc))


def payloads(messages: Messages) -> list[dict[str, Any]]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def filter_and_summarize(view_id: str) -> list[ScriptStep]:
    def summarize(messages: Messages) -> ModelResponse:
        new_view = payloads(messages)[-1]["result"]["view_id"]
        return calls(
            tool_call(
                "summarize",
                {"view_id": new_view, "columns": ["hours_per_week"], "metrics": ["mean"]},
            )
        )

    def answer(messages: Messages) -> ModelResponse:
        ids = [p["evidence_id"] for p in payloads(messages)]
        return calls(
            tool_call(
                "submit_answer",
                {
                    "explanation": "People over 40 work about 41 hours; 99 is unrelated.",
                    "claims": [{"text": "People over 40 average N hours.", "evidence_ids": ids}],
                    "caveats": ["Association only."],
                },
            )
        )

    return [
        calls(
            tool_call(
                "create_view",
                {"view_id": view_id, "filters": [{"column": "age", "op": "gt", "value": 40}]},
            )
        ),
        summarize,
        answer,
    ]


class Scripts:
    """llm_factory for the app: each new run takes the next queued script."""

    def __init__(self) -> None:
        self.queue: list[list[ScriptStep]] = []

    def __call__(self) -> FakeLLMClient:
        return FakeLLMClient(self.queue.pop(0) if self.queue else [])


@pytest.fixture
def scripts() -> Scripts:
    return Scripts()


@pytest.fixture
def api(settings: Settings, sample_ids: dict[str, str], scripts: Scripts) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm_factory=scripts)) as client:
        yield client


def new_conversation(client: TestClient) -> str:
    response = client.post(f"{API}/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def poll(client: TestClient, run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


def ask(client: TestClient, conversation_id: str, text: str = "Hours for people over 40?") -> Any:
    return client.post(f"{API}/conversations/{conversation_id}/messages", json={"content": text})


# --- full flow ----------------------------------------------------------------------------


def test_question_to_answer_over_http(
    api: TestClient, scripts: Scripts, sample_ids: dict[str, str]
) -> None:
    scripts.queue.append(filter_and_summarize(sample_ids["view_id"]))
    conv = new_conversation(api)
    accepted = ask(api, conv)
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] == "running" and body["links"]["run"] == f"{API}/runs/{body['run_id']}"

    run = poll(api, body["run_id"])
    assert run["status"] == "completed" and run["error"] is None
    assert run["question"]["id"] == body["message_id"]
    assert [op["tool_name"] for op in run["operations"]] == ["create_view", "summarize"]
    assert all(op["evidence_id"] for op in run["operations"])
    assert run["operations"][0]["output_ids"] == [run["operations"][1]["input_ids"][0]]

    (claim,) = run["claims"]
    assert claim["citation_origin"] == "agent_proposed"
    for evidence_id in claim["evidence_ids"]:
        evidence = api.get(f"{API}/evidence/{evidence_id}").json()
        assert evidence["run_id"] == run["id"] and evidence["result"]
    assert [a["scope"] for a in claim["assumptions"]["stated"]] == ["answer"]
    assert "mean_sensitivity" in {a["template_key"] for a in claim["assumptions"]["derived"]}

    answer = run["answer"]
    assert answer["verified"] is False
    # "40" also appears in the claim text; "41" and "99" appear only in the explanation.
    assert answer["unverified_numbers"] == ["41", "99"]
    assert run["config"]["prompt_version"] and "api_key" not in json.dumps(run["config"])


def test_graph_timeline_messages_and_related(
    api: TestClient, scripts: Scripts, sample_ids: dict[str, str]
) -> None:
    scripts.queue.append(filter_and_summarize(sample_ids["view_id"]))
    conv = new_conversation(api)
    run = poll(api, ask(api, conv).json()["run_id"])

    graph = api.get(f"{API}/runs/{run['id']}/graph").json()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in graph["edges"])
    assert {"SUPPORTS", "QUALIFIES", "CONSUMES", "PRODUCES"} <= {e["type"] for e in graph["edges"]}
    early = api.get(f"{API}/runs/{run['id']}/graph", params={"at_sequence": 2}).json()
    assert len(early["nodes"]) < len(graph["nodes"])

    # Timeline pagination walks every event exactly once, in order.
    seqs: list[int] = []
    after = 0
    while True:
        page = api.get(
            f"{API}/conversations/{conv}/timeline", params={"after": after, "limit": 3}
        ).json()
        seqs += [e["sequence_no"] for e in page["items"]]
        if page["next_after"] is None:
            break
        after = page["next_after"]
    assert seqs == list(range(1, run["last_event_sequence"] + 1))

    messages = api.get(f"{API}/conversations/{conv}/messages").json()["items"]
    assert [(m["role"], m["sequence_no"]) for m in messages] == [("user", 1), ("assistant", 2)]

    related = api.get(f"{API}/messages/{messages[1]['id']}/related").json()
    assert related["run_ids"] == [run["id"]]
    assert set(related["entities"]["claim"]) == {c["id"] for c in run["claims"]}
    assert set(related["entities"]["operation"]) == {o["id"] for o in run["operations"]}


# --- guards and errors ------------------------------------------------------------------


def test_one_running_run_per_conversation(
    api: TestClient, scripts: Scripts, sample_ids: dict[str, str]
) -> None:
    release = threading.Event()

    def blocked(messages: Messages) -> ModelResponse:
        assert release.wait(timeout=10)
        return calls(tool_call("submit_answer", {"explanation": "done"}))

    scripts.queue.append([blocked])
    conv = new_conversation(api)
    first = ask(api, conv).json()
    assert api.get(f"{API}/conversations/{conv}").json()["active_run_id"] == first["run_id"]

    busy = ask(api, conv, "another question")
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "CONVERSATION_BUSY"
    assert busy.json()["error"]["details"]["active_run_id"] == first["run_id"]

    other = new_conversation(api)  # other conversations are not blocked
    scripts.queue.append([calls(tool_call("submit_answer", {"explanation": "ok"}))])
    assert poll(api, ask(api, other).json()["run_id"])["status"] == "completed"

    release.set()
    assert poll(api, first["run_id"])["status"] == "completed"
    scripts.queue.append([calls(tool_call("submit_answer", {"explanation": "again"}))])
    assert ask(api, conv, "follow-up").status_code == 202


def test_failed_run_is_reported(api: TestClient, scripts: Scripts) -> None:
    scripts.queue.append([LLMUnavailableError("connection refused")])
    run = poll(api, ask(api, new_conversation(api)).json()["run_id"])
    assert run["status"] == "failed" and run["error"]["code"] == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    ("path", "payload", "status", "code"),
    [
        ("/conversations/conv_nope/messages", {"content": "q"}, 404, "UNKNOWN_CONVERSATION"),
        (None, {"content": ""}, 422, "VALIDATION_ERROR"),
        (None, {"content": "q", "model": "gpt"}, 422, "VALIDATION_ERROR"),
    ],
)
def test_message_errors(
    api: TestClient, path: str | None, payload: dict[str, Any], status: int, code: str
) -> None:
    url = f"{API}{path}" if path else f"{API}/conversations/{new_conversation(api)}/messages"
    response = api.post(url, json=payload)
    assert response.status_code == status and response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "url",
    [
        "/runs/run_nope",
        "/runs/run_nope/graph",
        "/evidence/ev_nope",
        "/messages/msg_nope/related",
        "/conversations/conv_nope/timeline",
    ],
)
def test_unknown_ids_are_404(api: TestClient, url: str) -> None:
    response = api.get(f"{API}{url}")
    assert response.status_code == 404 and response.json()["error"]["code"].startswith("UNKNOWN")


# --- restart and storage ---------------------------------------------------------------


def test_interrupted_runs_are_failed_on_startup(
    settings: Settings, session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    # A run that was started but never executed, as after a crash.
    pending = AgentOrchestrator(session_factory, FakeLLMClient([]), settings).start("Q")
    with TestClient(create_app(settings, llm_factory=Scripts())) as client:
        run = client.get(f"{API}/runs/{pending.run_id}").json()
        events = client.get(f"{API}/conversations/{pending.conversation_id}/timeline").json()
    assert run["status"] == "failed" and run["error"]["code"] == "INTERRUPTED"
    assert events["items"][-1]["event_type"] == "RUN_FAILED"


def test_app_engine_uses_wal(settings: Settings, migrated_db: str) -> None:
    engine = create_app_engine(migrated_db)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    finally:
        engine.dispose()
