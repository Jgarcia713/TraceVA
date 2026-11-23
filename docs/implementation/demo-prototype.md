# Interim-presentation prototype: frontend and replay demo mode (2026-11-23)

## Scope
A rough-draft frontend demonstrating the conversation → computation → chart pipeline with claim-level provenance, plus a demo mode that avoids waiting minutes for the local model. Decisions by the project owner:
- **Replay with a live backend** rather than static recorded data.
- **Backend-derived charts** rather than charts computed in the frontend.

## Demo mode (`LLM_PROVIDER=replay`)
- `ReplayLLMClient` (`backend/app/agent/replay.py`) returns the recorded model responses of real runs step by step. Everything else runs live: tool execution on the real data, validation, evidence, citation checks, assumptions, timeline, and graph.
- **ID remapping:**
  - Recorded evidence and view IDs are mapped to the live IDs by tool-result order.
  - Dataset IDs are mapped by dataset name, read from the dataset context the model sees.
  - So the catalog replays on any database holding the same data.
  - A citation that cannot be mapped is rejected by the normal check; replay never bypasses validation.
- **Honest labelling:** run config records `provider: "replay"`, `replayed_run_id`, and the original model and prompt version. The UI shows the demo notice.
- **Unknown questions:** the client binds to the question before the run starts, so a question outside the catalog is rejected with **422 `DEMO_UNKNOWN_QUESTION`**.
- **Catalog:** `backend/demo/catalog.json`, built by `scripts/export_demo.py` from 8 completed real runs. It holds model-visible responses only: no data rows, and no reasoning (never stored).
- **Separate database:** `scripts/start-demo.ps1` uses `demo.db`, so replayed runs never mix with the real-run records used for metrics; `report_runs.py` also excludes `replay` and `fake` providers.

## Charts
- `backend/app/analysis/charts.py` maps evidence to a small validated `ChartSpec` (bar charts; group or stack; number or percent; a denominator `n` per bar; notes such as truncation):
  - `summarize`: numeric metric per group, or category shares (stacked when grouped)
  - `inspect_dataset`: missing values per column
- `GET /claims/{id}/charts` builds charts from a claim's cited evidence. Single-value charts of the same measure are merged into one comparison labelled by view definition (e.g. "sex = Male" vs "sex = Female"), and an ungrouped bar is named after its view.
- The model does not choose charts yet; a `build_chart_spec` tool remains future work.

## Frontend
React 18 + TypeScript + Vite, Plotly (`plotly.js-dist-min`), React Flow (`@xyflow/react`), pinned versions. Details: `frontend/README.md`.

## Verification
- Backend: 189 tests, including chart mapping, replay with ID remapping (evidence, views, and dataset IDs from a "foreign" database), the up-front rejection of unknown questions, and the demo API end to end.
- **Replay on a fresh database:** all 8 catalog questions completed with claim texts identical to the original runs. The original model mistakes were re-validated and rejected live.
- **Browser walkthrough (Chrome automation):**
  - live progress with a repaired failure
  - the merged comparison chart ("difference of 6.02 hours" → Male 42.43 vs Female 36.41)
  - the stacked education chart, which shows the HS-grad level the claim omitted
  - the follow-up in the same conversation (chart labelled "age > 40, sex = Male / Female")
  - lineage highlighting and the timeline slider
  - no console errors
- Issues found and fixed during the walkthrough:
  - the graph did not refit as it grew
  - an ungrouped bar was labelled "all rows" instead of its view
  - chip tooltips replaced the questions as accessible names
  - the chat did not scroll to new answers

## Known limitations
- A rough draft: desktop layout only (designed for a 1080p projector); the graph layout is a simple column layout by node type.
- Plotly makes the production bundle large (> 500 kB); acceptable for a local demo.
- No frontend tests yet; types are checked with `tsc` and the build passes.
- Replay timing (0.6 s per step) is a display choice and says nothing about real model latency (median 162 s per question on the CPU laptop).
