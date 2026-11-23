# create_view: filtered dataset views (2026-11-23)

## Scope and behavior
A new agent tool `create_view(view_id, filters)` builds filtered views that `summarize` (and later tools) can use. It closes the gap found in the M2 evaluation, where the model approximated "age > 40" with `group_by age`. Decisions confirmed by the project owner: identical definitions reuse one view, and filters combine with AND only (`in` covers most OR needs). The full contract is in `docs/architecture.md`.

## Design choices
- **A view is identified by its definition.** A child view stores its *complete* canonical filter set. `definition_sha256` covers the source, canonical filters, columns, and null policy, and is unique per source, so filter order, `40` vs `40.0`, list order, and chaining (A, then B) versus a single call with A+B all resolve to the same `view_id`. `parent_view_id` records the base of the call that first created the view; every operation records its own lineage in `input_ids_json` / `output_ids_json`.
- **Missing values never match a comparison.** This includes `ne` and `not_in`; plain pandas would return True for `NaN != x`. Rows excluded for this reason are reported per filter.
- **Strict value types.** Pydantic's lax union mode coerced `true` into `1`, silently turning an invalid filter into a valid but different one. Filter values are now `StrictStr | StrictInt | StrictFloat`. A test caught this during development.
- **Tools stay read-only.** `create_view` returns a `ViewDraft`, and the orchestrator persists it in the same transaction as the Operation. A failed call writes no view.
- **Unknown-ID errors list the registered datasets' IDs**, following the same principle as `UNKNOWN_VALUE` suggestions: errors should name the valid options.
- **Prompt `m2-v2`**: use `create_view` for row restrictions and never approximate a filter with `group_by`; mention excluded rows.
- **Migration `0003_views`**: adds `dataset_views.definition_sha256` (NULL for root views; nothing backfilled) and `operations.output_ids_json` (`[]` for existing rows, which is accurate). Its downgrade is the project's first real table rebuild, now covered by the ADR-002 regression test.

## Tests and outcomes
- 144 tests passing:
  - filter semantics, validation, and canonicalization on hand-built frames
  - `create_view` against the fixture with stdlib reference values: counts, missing-value exclusions, reuse across order and chaining, replay after a cache reset, empty views, errors
  - agent flows: lineage from root to view to summary; a failed `create_view` writes no view; repair suggestions
  - the table-rebuild migration with real parent and child rows
- Mutation check: leaving FKs on during migrations makes the rebuild test fail with `FOREIGN KEY constraint failed`.

## Real-model runs (qwen3.5:4b, num_ctx 8192, thinking off, prompt m2-v2, full Adult data)

| Question | Outcome | Ops (failed) | Time | Correctness |
|---|---|---|---|---|
| Mean weekly hours for people older than 40 (failed in M2) | completed | 2 (0) | 132 s | **Correct**: 41.29 h, n = 13,443. Cited both the view and the summary evidence; mentioned 0 rows excluded for missing values |
| Share earning >50K among women with a Bachelors degree (run 1) | **failed: BUDGET_EXHAUSTED** (6 steps) | 6 (4) | 170 s | Invented `view_1` four times, then used the root view; the correct view and computation were not reached before the budget ran out |
| Same question (run 2, after unknown-ID errors started listing valid IDs) | **failed: BUDGET_EXHAUSTED** (6 steps) | 6 (3) | 143 s | Invented `view_1`, `view_2`, `view_3`, then used the correct root view, created the correct view, and computed **20.9 % of 1,619** (verified correct), one step short of submitting |

## Findings
1. **Filtering works end to end** where the model uses real IDs. The M2 failure case is now answered correctly.
2. **Inventing IDs is the new dominant failure mode** for this 4B model on multi-filter questions. Listing valid IDs in the error helped only after 2–3 more attempts. This is a model-reliability property to measure in evaluation (invented-ID rate, steps to recover), not something to patch per question.
3. **Six steps is tight for filter questions.** The minimum path is `create_view` → `summarize` → `submit_answer` (3 steps); one repair or an extra `inspect_dataset` leaves little slack.

## Follow-up decisions (project owner, 2026-11-23)
- **Done:** the default `MAX_AGENT_STEPS` is raised from 6 to 8.
- **Monitor, not implemented:** how often the model invents IDs, to be measured in evaluation. Candidate mitigations if it stays common:
  - Dataset names as aliases for root views (e.g. `adult`).
  - Run-scoped short aliases (e.g. `v1`, `e1`) that the orchestrator maps to full IDs. Lineage and the database keep the full IDs. Notably, the invented IDs (`view_1`, `view_2`) already look like short aliases, so this may match the model's expectations. Open question: whether claims would cite aliases (resolved on the server) or full IDs.
- **Open:** compare `qwen3.5:9b` or thinking mode, ideally on the RTX 4070 laptop.
