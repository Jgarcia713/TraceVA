# TraceVA architecture (as built)

The design target is `docs/TRACEVA_BACKEND_CONTEXT.md`. This document describes what currently exists; decisions and their rationale are in `docs/decisions/`.

**Status:** Milestones 0–3 complete (2026-11-23): agent runs over HTTP with polling, persisted timeline and assumptions, computed provenance graph. Challenge branches (M4) are not built yet. The agent runs from the CLI only. Timeline events, assumptions, the provenance graph API, HTTP endpoints for asking questions, and challenge branches are not built yet.

## Components

```text
scripts/ask.py ──► app/agent/orchestrator.py ──► LLMClient (OpenAI SDK → Ollama /v1)
                         │   prompts.py, tool_registry.py (schemas, hash, execute)
                         ▼
FastAPI (app/main.py)  see "HTTP API" below; runs execute in background threads
                       (app/agent/runner.py: RunManager, startup reconciliation)
   │
   ├── app/api/            routes, error envelope + request IDs (errors.py)
   ├── app/analysis/
   │     tools.py          inspect_dataset, create_view, summarize: typed in/out; resolve IDs → frames
   │     views.py          pure filter AST: validation, canonical definitions, application
   │     statistics.py     pure dataframe functions (no DB/files): profile, summarize
   │     registry.py       code-defined datasets + registration (source + root view)
   │     datasets.py       parser adult-v1, SHA-256 checks, path safety, frame cache
   ├── app/provenance/     timeline.py (events), templates.py (method caveats),
   │                       graph.py (computed graph, claim assumptions), text_checks.py
   ├── app/schemas/        Pydantic contracts: tools.py (LLM-facing tool inputs), agent.py, api.py
   └── app/db/             models, UTC datetime type, engines (FK + transaction handling)
SQLite (Alembic-managed) ← datasets, conversations, runs, model calls, operations, evidence, claims
datasets/ (gitignored raw files, hash-pinned)
```

## HTTP API (`/api/v1`)

| Method and path | Purpose |
|---|---|
| `GET /health` | Database reachability and schema revision vs. head |
| `GET /datasets`, `GET /datasets/{id}` | Registered sources, root view IDs, column schema |
| `POST /conversations` | Start a conversation |
| `GET /conversations/{id}` | Includes `active_run_id` |
| `GET /conversations/{id}/messages?after&limit` | Chat history. Assistant messages carry `verified: false` and `unverified_numbers` |
| `POST /conversations/{id}/messages` | Ask a question: **202** with `run_id` and poll links. **409 `CONVERSATION_BUSY`** while a run is active. **404** for an unknown conversation |
| `GET /runs/{id}` | Status (poll target), operations, claims with citations and assumptions (stated with scope, derived), answer, usage, config snapshot |
| `GET /runs/{id}/graph?at_sequence=N` | Computed graph; a snapshot when `at_sequence` is given |
| `GET /evidence/{id}` | Full stored result of an operation |
| `GET /conversations/{id}/timeline?after&limit` | Ordered events (cursor pagination by `sequence_no`) |
| `GET /messages/{id}/related` | Chat selection → the run's entity IDs grouped by type, plus event sequence numbers |

Runtime rules:
- **Background execution:** a `ThreadPoolExecutor` (`MAX_CONCURRENT_RUNS`, default 2). Budgets start when a run begins executing, not while it is queued; a queued run is still shown as `running`.
- **One running run per conversation:** checked in code, and enforced by a partial unique index so concurrent requests cannot race past the check.
- **Single process only:** at startup, runs left `running` are marked `failed` with `INTERRUPTED` and a `RUN_FAILED` event. Shutdown waits for in-flight runs and cancels queued ones (the next startup marks those as interrupted).
- **SQLite WAL mode** plus a 5 s busy timeout on the app engine, so API reads proceed while a run writes.

## Data model

| Table | Purpose | Notes |
|---|---|---|
| `dataset_sources` | Registered immutable file | Unique on (name, sha256, parser_version); `schema_json` holds column name/kind/dtype |
| `dataset_views` | Reproducible selection of a source | Exactly one **root view** per source. Child views store their *complete* canonical filter set and are unique per `(source_id, definition_sha256)`, so identical definitions are reused regardless of filter order or chaining |
| `conversations`, `chat_messages` | Dialogue | Question message carries `run_id`; one question per run (partial unique index) |
| `analysis_runs` | One investigation | `running → completed/failed`; config snapshot (model, prompt version, tool-schema hash, budgets, dataset hashes); typed `error_json` |
| `model_calls` | Each model request | Content, raw tool calls, usage, latency, `reasoning_chars_discarded` (reasoning text never stored) |
| `operations` | Each tool call, including failed ones and rejected `submit_answer` attempts | Written once; raw arguments as sent, validated args or null, consumed IDs (`input_ids_json`), produced or resolved IDs such as views (`output_ids_json`), error |
| `evidence` | Result of a successful operation | One per successful operation; immutable |
| `claims`, `claim_evidence` | Model claims and their citations | Citations `agent_proposed`; must reference evidence from the same run (checked on the server) |
| `assumptions`, `assumption_links` | Stated caveats (`agent_stated`) and backend method caveats (`method_template`) | Always `not_tested`. A link qualifies exactly one claim, operation, or answer message (typed FKs + CHECK). Answer-level caveats link to the answer, never to each claim |
| `timeline_events` | Ordered event log per conversation | Emitted in the same transaction as the entity; `sequence_no` unique per conversation; `message_id` = triggering chat message |

IDs are type-prefixed UUIDs (`src_…`, `view_…`). Timestamps are timezone-aware UTC (`UTCDateTime` rejects naive values). There are no cascade deletes.

## Tool contracts

- Inputs use `extra="forbid"`: unknown arguments are rejected, not ignored.
- Failures raise `ToolError` with a machine-readable `ToolErrorCode` (`UNKNOWN_SOURCE`, `UNKNOWN_VIEW`, `UNKNOWN_COLUMN`, `METRIC_NOT_APPLICABLE`, `UNSUPPORTED_VIEW`, `DATASET_INTEGRITY`).
- `inspect_dataset(source_id)` returns the row count, root view ID, hash, and parser version, plus per-column kind, missingness, distinct count, numeric min/max, and the top 5 categories.
- `summarize(view_id, columns, group_by≤2, metrics)` has these semantics:
  - `n` is always the non-missing count and the proportion denominator; `n_missing` is always reported.
  - Missing group keys form an explicit `null` group.
  - `std` is the sample standard deviation (ddof=1), `null` when n < 2.
  - Every metric must apply to every column (strict). Mixed numeric and categorical requests are separate calls, and therefore separate future operations.
  - A column in both `columns` and `group_by` is **rejected, not silently corrected** (decided 2026-11-22). Normalizing model arguments would make the recorded operation differ from what the model actually requested. Prevention is handled in the field descriptions, which the model sees as JSON Schema. In a pre-M2 test with qwen3.5:4b, the model made this mistake before the descriptions were improved and repaired it in one round after receiving the error; with the improved descriptions it called correctly on the first try (one question, temperature 0).
  - Groups are ordered by size descending, then key. Truncation at `MAX_TOOL_RESULT_ROWS` keeps the largest groups and reports `total_groups` and `truncated`. Categories beyond the cap are reported as `other_count`.
  - An empty view returns an explicit empty result with a warning rather than an error.
- `create_view(view_id, filters)`:
  - Filters are `{column, op, value}`, combined with AND. Operators: `eq ne lt le gt ge in not_in is_missing not_missing`. Order operators require numeric columns.
  - Values use strict types: `true` is rejected, not coerced to 1. Category values must match exactly; unknown values fail with `UNKNOWN_VALUE` and spelling suggestions.
  - A missing value never matches a comparison (including `ne` and `not_in`). Rows excluded for that reason are reported per filter (`rows_excluded_missing`).
  - Returns `view_id`, `reused`, base and new row counts, the complete canonical filters, and warnings (empty view, or fewer than 30 rows).
  - The tool never writes. It returns a view draft that the orchestrator persists in the same transaction as the Operation; direct callers use `persist_views`.

## Deliberate boundaries

- **No HTTP endpoint executes tools.** Tools run either directly (tests and scripts, unrecorded) or through the orchestrator (recorded). HTTP endpoints for asking questions arrive in M3.
- **Follow-up context:** a run sees up to 3 earlier questions with their claims (never explanations); claims must cite current-run evidence (ADR-005).
- **The provenance graph is computed**, never stored (`app/provenance/graph.py`). Snapshots at sequence N come from timeline events; temporal order is never an edge.
- **Tool results shown to the model** omit fields a tool never set but keep explicit nulls (e.g. the missing-value group key).
- **The agent's explanation text is not verified.** Only claims carry citations. See `docs/implementation/m2-agent-vertical-slice.md`.
- `statistics.py` is pure, so correctness tests use hand-built frames with hand-computed values. Integration tests compare tool output against a stdlib-only reading of the raw fixture (`tests/reference.py`), not pandas against pandas.
- Only root views are supported; any other view raises `UNSUPPORTED_VIEW` rather than being misapplied.

## Measured (local, Iris Xe laptop, full `adult.data`)

- First load including hashing and parsing: about 0.07 s. `summarize` grouped by `sex`: about 0.01 s. Grouped by `fnlwgt` (21,648 groups, truncated to 30): about 0.14 s, with a 5.7 KB JSON payload.
