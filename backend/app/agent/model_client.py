"""LLM adapter boundary (brief §3).

The orchestrator depends only on `LLMClient` and the normalized `ModelResponse`. Provider
quirks stay here. Reasoning/thinking text, if a provider returns it, is discarded in
this module and never reaches the orchestrator or the database (brief invariant 8);
only its length is reported, so leakage stays measurable.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import urlparse

import openai
from pydantic import BaseModel, Field

from app.config import Settings

Message = dict[str, Any]
REASONING_FIELDS = ("reasoning", "reasoning_content", "thinking")


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: str = Field(description="Raw JSON string exactly as returned by the model.")


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0
    reasoning_chars_discarded: int = 0


class LLMUnavailableError(RuntimeError):
    """Transport failure, timeout, or provider error after the client's own retries."""


class LLMClient(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> ModelResponse:
        """`timeout_s` caps this request (per attempt), e.g. to the run's remaining budget."""
        ...

    def describe(self) -> dict[str, Any]:
        """Configuration snapshot for run metadata. Must not contain secrets."""
        ...


def _arguments_to_str(arguments: Any) -> str:
    # OpenAI-compatible endpoints return a JSON string; be defensive about dicts.
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments)


class OpenAICompatClient:
    """Chat Completions over the OpenAI SDK (Ollama's /v1, or a hosted provider)."""

    def __init__(self, settings: Settings, http_client: Any = None) -> None:
        self._settings = settings
        self._client = openai.OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,  # SDK retries transport errors/429/5xx
            http_client=http_client,
        )

    def describe(self) -> dict[str, Any]:
        s = self._settings
        return {
            "provider": s.llm_provider,
            "transport": "openai-chat-completions",
            "base_url_host": urlparse(s.llm_base_url).netloc,
            "model": s.llm_model,
            "temperature": s.llm_temperature,
            "thinking": s.llm_thinking,
            "seed": s.llm_seed,
            "timeout_s": s.llm_timeout_s,
            "max_retries": s.llm_max_retries,
            "openai_sdk_version": openai.__version__,
        }

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> ModelResponse:
        s = self._settings
        kwargs: dict[str, Any] = {
            "model": s.llm_model,
            "messages": list(messages),
            "tools": list(tools),
            "temperature": s.llm_temperature,
        }
        if s.llm_seed is not None:
            kwargs["seed"] = s.llm_seed
        if timeout_s is not None:
            kwargs["timeout"] = min(timeout_s, s.llm_timeout_s)
        if not s.llm_thinking:
            # Verified with Ollama 0.40 + qwen3.5 (docs/implementation/pre-m2-model-smoke-test.md)
            kwargs["extra_body"] = {"reasoning_effort": "none"}

        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            raise LLMUnavailableError(f"provider returned HTTP {exc.status_code}") from exc
        except openai.OpenAIError as exc:  # connection errors, timeouts
            raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not completion.choices:
            raise LLMUnavailableError("provider returned no choices")
        choice = completion.choices[0]
        message = choice.message
        extra = message.model_extra or {}
        discarded = sum(len(str(extra.get(f) or "")) for f in REASONING_FIELDS)

        tool_calls = [
            ToolCallRequest(
                id=tc.id or f"call_{uuid.uuid4().hex[:12]}",
                # A nameless call becomes an UNKNOWN_TOOL error the model can repair,
                # rather than a validation crash that loses the model call record.
                name=tc.function.name or "",
                arguments=_arguments_to_str(tc.function.arguments),
            )
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        usage = completion.usage
        return ModelResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            latency_ms=latency_ms,
            reasoning_chars_discarded=discarded,
        )


# ---------------------------------------------------------------------------
# Deterministic fake for tests and offline demos
# ---------------------------------------------------------------------------

ScriptStep = ModelResponse | Exception | Callable[[Sequence[Message]], ModelResponse]


class FakeLLMClient:
    """Replays a script. Callable steps see the conversation so far, which lets them cite
    evidence IDs that only exist at runtime. Records every request for assertions."""

    def __init__(self, script: Sequence[ScriptStep]) -> None:
        self._script = list(script)
        self.requests: list[list[Message]] = []
        self.timeouts: list[float | None] = []

    def describe(self) -> dict[str, Any]:
        return {"provider": "fake", "model": "scripted", "steps": len(self._script)}

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> ModelResponse:
        self.requests.append(json.loads(json.dumps(list(messages))))
        self.timeouts.append(timeout_s)
        if not self._script:
            raise AssertionError("FakeLLMClient script exhausted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages)
        return step


def tool_call(
    name: str, arguments: dict[str, Any] | str, call_id: str | None = None
) -> ToolCallRequest:
    """Test helper: build a tool call with JSON-encoded arguments."""
    return ToolCallRequest(
        id=call_id or f"call_{uuid.uuid4().hex[:8]}",
        name=name,
        arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
    )
