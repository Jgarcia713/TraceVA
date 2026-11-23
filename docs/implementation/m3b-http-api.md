# M3b: HTTP API with background runs (2026-11-23)

## Scope
REST endpoints for conversations, questions, runs, the provenance graph, evidence, the timeline, and chat-to-graph resolution. Questions return **202 Accepted** and the client polls the run, because a question takes 1.5–7 minutes on the local CPU model. The endpoint table and runtime rules are in `docs/architecture.md` ("HTTP API").

## Components
- `app/agent/orchestrator.py`: `ask()` is split into `start()`, which is synchronous, persists the question and run, and raises `UnknownConversationError` / `ConversationBusyError`, and `run()`, which never raises. Budgets start in `run()`.
- `app/agent/runner.py`: `RunManager` (thread pool, `MAX_CONCURRENT_RUNS`) and `reconcile_interrupted_runs` (startup).
- `app/api/conversations.py`, `app/api/runs.py`, `app/api/views.py` (read models), `app/api/deps.py`.
- `app/main.py`: `create_app(settings, llm_factory)`, so tests inject a fake model; the lifespan handles reconciliation, the run manager, and shutdown.
- Migration `0005_run_guard`: a partial unique index allowing one `running` run per conversation.
- SQLite WAL mode and a 5 s busy timeout on the app engine.

## Design choices
- **One active run per conversation is enforced by the database.** An application-level check gives a friendly 409 with `active_run_id`; the partial unique index closes the race between two simultaneous requests. A follow-up only makes sense after the previous answer exists.
- **Interrupted runs.** A run cannot survive a process restart, so startup marks leftover `running` runs as `failed` (`INTERRUPTED`) with a `RUN_FAILED` event instead of leaving them stuck. This assumes a single server process.
- **Polling rather than server-sent events**, as the brief prefers one mechanism; the timeline's `after` cursor serves incremental updates.
- **No secrets in responses:** the run config snapshot comes from `LLMClient.describe()`, and a test checks that `api_key` does not appear.

## Tests
171 passing, 14 of them HTTP tests driving the real thread pool with polling:
- the full flow and its endpoints
- timeline pagination over every event
- chat-message → entity resolution
- 409 while busy, with other conversations unaffected and the conversation usable again after completion
- a failed run reported correctly
- validation errors and 404s
- startup reconciliation
- WAL and busy-timeout pragmas

## Real end-to-end check (uvicorn + qwen3.5:4b, 2026-11-23)
- `POST /conversations/{id}/messages` returned 202 immediately; a second post in the same conversation while running returned **409**.
- Question: "What share of people older than 40 earn more than 50K?" The run completed after about 144 s (3 operations, 1 failed). The model invented `view_older_than_40` once, then recovered.
- **Correct:** the claim "35.36% … earn more than $50K" and "13,443 rows" match the direct computation (35.4%, n = 13,443).
- The view already existed from earlier runs, so it was reused: no `VIEW_CREATED` event, and the graph shows it as pre-existing (`created_seq = null`).
- No method-template caveats applied (proportions only, no missing values), so `derived` was empty, which is correct.
- **Known limitation of the soft number check:** the explanation's "35.4" is listed as unverified because the claim says "35.36". The check matches exactly, so rounding variants are flagged. A rounding-tolerant comparison is a cheap follow-up.

## Remaining risks and follow-ups
- Single-process assumption; multiple workers would mis-reconcile runs that are still executing.
- A queued run shows `running` (no separate `queued` status).
- Shutdown waits for in-flight runs, so a real model call delays Ctrl-C until it finishes.
- Starlette's `httpx` deprecation warning in the test client remains (ADR-003).

## Code review fixes (2026-11-23)
A second independent review of M3 found 10 issues, mostly caused by parallel background runs. All were fixed with regression tests (178 tests passing); the two most important fixes were mutation-checked.

| Issue | Fix |
|---|---|
| Two runs creating the same view at once: the second failed on the unique index | `_resolve_views` reuses the committed view inside the write transaction |
| WAL read-then-write transactions could fail immediately (`SQLITE_BUSY_SNAPSHOT`) | `write_transaction()` uses `BEGIN IMMEDIATE`; reads stay deferred |
| A crash while recording a failure left a run `running`, blocking its conversation | The run manager's done-callback logs it and marks the run failed |
| A startup reconcile could be overwritten by a late answer from a CLI run | Completing or failing a run is a no-op unless it is still `running` |
| Derived assumptions leaked across runs and conversations through reused views | Lineage traversal is restricted to the claim's own run |
| The number check also scanned the appended answer caveats | `explanation_part()` checks the explanation only |
| Startup reconciliation errors were swallowed | Only "no such table" is tolerated; other errors are logged and reported by `/health` (`startup_errors`) |
| A caveat on both a claim and the answer was listed twice | Merged by assumption ID, preferring claim scope |
| Shutdown could hang until a run finished | A stop flag is checked between steps (`INTERRUPTED`); a model call in progress still completes |
| A graph snapshot showed the run's final status | The status is derived from timeline events at or before `at_sequence` |

The default `MAX_CONCURRENT_RUNS` changed from 2 to 1, because a local model serves one request at a time. The HTTP tests use 2 workers so that the parallel path stays covered.
