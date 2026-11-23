# TraceVA architecture (as built)

The design target is `TRACEVA_BACKEND_CONTEXT.md`. This document describes what currently exists; decisions and their rationale are in `docs/decisions/`.

**Status:** Milestones 0–2 complete (2026-11-22). The agent runs from the CLI only. Timeline events, assumptions, the provenance graph API, HTTP endpoints for asking questions, and challenge branches are not built yet.

## Components

```text
scripts/ask.py ──► app/agent/orchestrator.py ──► LLMClient (OpenAI SDK → Ollama /v1)
                         │   prompts.py, tool_registry.py (schemas, hash, execute)
                         ▼
FastAPI (app/main.py)  /api/v1/health, /api/v1/datasets[/{id}]   read-only
   │
   ├── app/api/            routes, error envelope + request IDs (errors.py)
   ├── app/analysis/
   │     tools.py          inspect_dataset, summarize: typed in/out; resolve IDs → frames
   │     statistics.py     pure dataframe functions (no DB/files): profile, summarize
   │     registry.py       code-defined datasets + registration (source + root view)
   │     datasets.py       parser adult-v1, SHA-256 checks, path safety, frame cache
   ├── app/schemas/        Pydantic contracts: tools.py (future LLM-facing), api.py
   └── app/db/             models, UTC datetime type, engines (FK + transaction handling)
SQLite (Alembic-managed) ← dataset_sources, dataset_views
datasets/ (gitignored raw files, hash-pinned)
```

## Data model

| Table | Purpose | Notes |
|---|---|---|
| `dataset_sources` | Registered immutable file | Unique on (name, sha256, parser_version); `schema_json` holds column name/kind/dtype |
| `dataset_views` | Reproducible selection of a source | Exactly one **root view** per source (partial unique index); child views arrive with `create_view` |
| `conversations`, `chat_messages` | Dialogue | Question message carries `run_id`; one question per run (partial unique index) |
| `analysis_runs` | One investigation | `running → completed/failed`; config snapshot (model, prompt version, tool-schema hash, budgets, dataset hashes); typed `error_json` |
| `model_calls` | Each model request | Content, raw tool calls, usage, latency, `reasoning_chars_discarded` (reasoning text never stored) |
| `operations` | Each tool call, including failed ones | Written once; raw arguments as sent, validated args or null, consumed IDs, error |
| `evidence` | Result of a successful operation | One per successful operation; immutable |
| `claims`, `claim_evidence` | Model claims and their citations | Citations `agent_proposed`; must reference evidence from the same run (checked on the server) |

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

## Deliberate boundaries

- **No HTTP endpoint executes tools.** Tools run either directly (tests and scripts, unrecorded) or through the orchestrator (recorded). HTTP endpoints for asking questions arrive in M3.
- **The agent's explanation text is not verified.** Only claims carry citations. See `docs/implementation/m2-agent-vertical-slice.md`.
- `statistics.py` is pure, so correctness tests use hand-built frames with hand-computed values. Integration tests compare tool output against a stdlib-only reading of the raw fixture (`tests/reference.py`), not pandas against pandas.
- Only root views are supported; any other view raises `UNSUPPORTED_VIEW` rather than being misapplied.

## Measured (local, Iris Xe laptop, full `adult.data`)

- First load including hashing and parsing: about 0.07 s. `summarize` grouped by `sex`: about 0.01 s. Grouped by `fnlwgt` (21,648 groups, truncated to 30): about 0.14 s, with a 5.7 KB JSON payload.
