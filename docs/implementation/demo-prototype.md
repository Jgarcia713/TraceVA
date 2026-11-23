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

## Modern-web-guidance pass (2026-11-23)
Guides consulted with the `modern-web-guidance` plugin: `accessibility`, `spinner`, `slider`, `css-layout`, `improve-text-layout-and-legibility`, `performance`. Applied:
- **Live updates:**
  - one polite live region announces answer completion or failure (not loading states)
  - the ask error is `role="alert"`
  - the busy indicator is a native indeterminate `<progress>`, slowed under `prefers-reduced-motion` and drawn in system colors under forced colors
- **Charts:** a data table (`<details>`, with each row's `n`) under every chart as its accessible alternative; the Plotly canvas is `aria-hidden`.
- **Labels and structure:** a real `<label>` for the question input (the placeholder had been the only label), an `<h1>`, `aria-valuetext` and `accent-color` on the timeline slider.
- **Sizes and contrast:** minimum 24 px targets for small buttons, chips, and summaries; the loose-number text darkened to at least 4.5:1.
- **Layout:** `scrollbar-gutter: stable` and `overscroll-behavior: contain` on panel scrollers; `text-wrap: balance` on headings and `pretty` on answer and claim text.
- **Performance** (own decision, not from a guide): Plotly is loaded by dynamic `import()` on first chart and switched to the official basic bundle (bar, scatter, pie). The initial JS is 337 kB (110 kB gzip), down from ~5 MB; the Plotly chunk is 1.1 MB, down from 4.7 MB.
- **Bug found while verifying:** the visually hidden table `<caption>` (absolutely positioned) escaped the scroll containers and made the whole page scroll by 36 px. Panels are now `position: relative`; the document height equals the viewport again.
- **Browser-verified:** label association, live-region text, no unnamed buttons, data table contents (it shows the omitted HS-grad row: 16.0%, n = 10,501), and the lazy chart rendering.

The guidance tool requires Node 22; it was run through a temporary `npx -p node@22`, and the system Node (20.19) was unchanged.

## Code review fixes (2026-11-23)
An independent review of the prototype branch found 10 issues (5 important); all were fixed (192 backend tests). The review also traced the replay ID alignment and found it sound: every tool message maps to exactly one operation.

| Issue | Fix |
|---|---|
| A failed poll left the UI busy forever (input and chips disabled) | Polling retries with backoff and shows a "retrying" alert; after about a minute it stops with a clear message. Polling starts before the message refresh. **Browser-verified:** with the backend killed mid-run, the alert appeared; after a restart, the run came back as `INTERRUPTED`, the alert cleared, and the input re-enabled |
| The active run's poll overwrote the graph of an older run being inspected; on completion the wrong claim was selected | The poll updates the graph only when that run is selected, and auto-selects the new claim only then |
| Fast claim or run switching could show stale charts or graphs | Both fetch effects ignore responses that arrive after the selection changed |
| Grouped proportion charts drew categories cut by the size cap as 0% | Shown as unknown (a gap) with a note; drawn as 0 only when the group truncated nothing |
| Claim-chart merging combined different measures (all proportion charts share one y label) | The merge key is the measure (title plus category or column); duplicate view labels get evidence IDs |
| A non-JSON error (proxy 502/504) surfaced as a raw parse error; a failed start left Ask silently inert | Robust error parsing with a generic envelope; start-up failures show a banner |
| Demo mode returned 422 instead of 404 for an unknown conversation | The conversation is checked before the replay bind; docstrings updated |
| The run report mis-rendered `\|` or newlines in questions and counted missing token data as 0 | Cells escaped; median over runs with token data only |
| The demo launcher ignored failed setup steps (PowerShell 5.1 does not stop on native exit codes) | Each step checks `$LASTEXITCODE` and aborts before starting servers |
| The TypeScript build cache was committed | Untracked and gitignored |

## Known limitations
- A rough draft: desktop layout only (designed for a 1080p projector); the graph layout is a simple column layout by node type.
- The Plotly chunk is still 1.1 MB, loaded on first chart.
- No frontend tests yet; types are checked with `tsc` and the build passes.
- Replay timing (0.6 s per step) is a display choice and says nothing about real model latency (median 162 s per question on the CPU laptop).
