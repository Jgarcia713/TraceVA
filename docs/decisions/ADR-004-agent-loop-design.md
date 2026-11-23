# ADR-004: Agent loop design for Milestone 2

Status: Accepted
Date: 2026-11-22

## Context
Milestone 2 requires that a local model choose and execute a real tool, and that a claim reference a real evidence ID, with the same path testable without a model (brief §12). Real evidence IDs require persisted operations and evidence, which the brief schedules for M3. The pre-M2 smoke test (`docs/implementation/pre-m2-model-smoke-test.md`) established that Ollama's OpenAI-compatible endpoint supports tool calls and that `reasoning_effort: "none"` disables thinking.

## Options and decisions
1. **Scope.** Move the core execution records (conversations, chat messages, runs, model calls, operations, evidence, claims, claim–evidence links) into M2. Timeline events, assumptions, the graph API, and revisions stay in M3/M4. *Alternative:* in-memory evidence for M2, which would produce IDs that are not real.
2. **Final answer.** A terminal `submit_answer` tool call (`explanation`, `claims[{text, evidence_ids}]`, `caveats`), validated like any tool, with a citation check on the server. *Alternative:* JSON in the reply text, which has weaker structure and its own parsing and repair path.
3. **Transport.** The OpenAI Python SDK behind an `LLMClient` protocol, using its built-in timeouts and retries. Revisit if more control over error messages is needed. *Alternative:* raw `httpx`.
4. **Entry point.** A CLI (`scripts/ask.py`) plus a service class for M2; HTTP endpoints move to M3.
5. **Context window.** The model is a derived Ollama model with `num_ctx` pinned (`backend/ollama/*.Modelfile`), so the window is part of the recorded model tag. The orchestrator fails a run with `CONTEXT_EXHAUSTED` before a request would exceed `LLM_CONTEXT_TOKENS - LLM_OUTPUT_RESERVE_TOKENS`, and also when the server reports a prompt at that limit. Ollama defaulted to 4,096 tokens and silently drops the oldest context. *Alternatives:* a global `OLLAMA_CONTEXT_LENGTH` setting, which is invisible in run metadata; LLM summarization of older turns, which is lossy and can alter numbers that claims then repeat (deferred; see Consequences).

## Rationale
The citation check, immutable operation records, and loud failures are the project's core guarantees, so they are implemented directly rather than delegated to a framework. LangChain/LangGraph are excluded by the brief, and a framework-managed message history would compete with the database as the authoritative record.

## Consequences
- Operations are written once, after execution (`succeeded`/`failed`), and never updated. Evidence exists only for successful operations. Model mistakes are recorded with the raw arguments exactly as sent.
- Reasoning text is discarded in the client adapter; only `reasoning_chars_discarded` is stored.
- Run metadata records the model configuration (no secrets), `PROMPT_VERSION`, `tool_schema_hash`, budgets, and dataset hashes.
- Context compaction (pinned prefix, deterministic stubs for older tool results, and a `get_evidence` retrieval tool) is deferred until challenge runs (M4) need it.

## Verification
- 96 tests passing (no model access). Mutation checks confirmed that tests fail when (a) citation validation is removed, (b) a write transaction is held across a model call, and (c) reasoning text leaks into stored content.
- Manual evaluation with qwen3.5:4b on the full Adult data: `docs/implementation/m2-agent-vertical-slice.md`.
