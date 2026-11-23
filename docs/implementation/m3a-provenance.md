# M3a: provenance data layer (2026-11-23)

## Scope
Timeline events, assumptions (agent-stated and method-template), a computed provenance graph with snapshots, conversation context for follow-ups, and a soft check of numbers in the explanation. There are no HTTP endpoints yet (M3b). Design and rationale: ADR-005.

## Components
- `app/provenance/timeline.py`: `EventType`, `EntityType`, and `emit()`, called inside the entity's own transaction.
- `app/provenance/templates.py`: pure method caveats per tool:
  - `summarize`: `mean_sensitivity`, `groups_truncated`, `missing_in_summary`, `small_groups`
  - `create_view`: `missing_excluded` (with counts), `small_view`
- `app/provenance/graph.py`:
  - `run_graph(session, run_id, at_sequence)`: computed nodes and edges, each edge with its origin
  - `claim_assumptions(session, claim_id)`: stated assumptions with `scope` (claim or answer), and derived assumptions found by lineage traversal, merged by text
- `app/provenance/text_checks.py`: `unverified_numbers(explanation, claim_texts)`.
- Orchestrator: emits events, records assumptions (agent-stated text deduplicated per run), and builds the follow-up context (last 3 turns: questions and claims only).
- `submit_answer`: optional per-claim `caveats`. Prompt version `m3-v1`.
- Migration `0004_provenance`: `assumptions`, `assumption_links` (typed FKs + CHECK), `timeline_events`.
- `scripts/ask.py`: `--conversation` restored. Output labels the explanation as unverified, lists loose numbers, and shows stated and derived assumptions per claim.

## Tests
157 passing. Mutation checks: fanning answer caveats out to every claim, and disabling upstream traversal, each fail exactly the intended test.

## Real-model check (qwen3.5:4b, 8k context, prompt m3-v1): a two-turn conversation

| Turn | Outcome | Ops (failed) | Time | Correctness |
|---|---|---|---|---|
| "How do average weekly working hours differ between men and women?" | completed | 5 (1) | 230 s | Correct (42.43 / 36.41; the std values 12.12 / 11.81 were also correct). Invented `view_men_1234567890abcdef` once, then recovered. |
| Follow-up: "And what about only people older than 40?" | completed | 5 (0) | 158 s | **Correct**: 42.88 vs 37.46, n = 9,497 / 3,946. The subject (men vs women) was resolved from the earlier turn's claims. |

## Observations
1. **Follow-up context works.** The second question has no subject of its own, and the model resolved it from the earlier claims.
2. **The model wrote zero per-claim caveats** in both turns; everything went to answer-level. This is the expected weak-model behaviour, and fill rate is now measurable per model (ADR-005, decision 4).
3. **Some claims contain numbers the model derived by arithmetic** ("difference of 6.02", "5.42"). The arithmetic is correct, but these numbers appear in no evidence. A future check of claim numbers against evidence must treat model-derived arithmetic separately, either flagging it or adding a `compare` tool so differences are computed by the backend.
4. **The loose-number check flags true values** (std, median, min/max from the evidence) as well as values only the explanation contains. It correctly reports "not tied to a claim", not "wrong"; the UI should present it that way.
5. **An agent-stated caveat can be vacuous where the backend is precise.** The agent wrote "rows with missing age are excluded"; the backend, knowing 0 rows were excluded, emitted no `missing_excluded` caveat. Showing both sources side by side makes the difference visible.
6. **Answer-level caveats are reported with `scope: "answer"`** per claim, so a reader sees that they were not tied to that claim, and identical template caveats from several operations are merged.
