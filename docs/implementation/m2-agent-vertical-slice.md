# Milestone 2: agent vertical slice (2026-11-22)

## Scope and behavior
`python scripts/ask.py "<question>"` runs one agent investigation. Each run records:
- the question and answer as chat messages
- every model call: content, raw tool calls, token usage, latency, and the length of discarded reasoning
- every tool call as an operation, with evidence for each successful one
- claims, each linked to the evidence it cites

Design decisions are in ADR-004.

## Components
- `app/agent/model_client.py`: `LLMClient` protocol, `OpenAICompatClient` (OpenAI SDK, discards reasoning), `FakeLLMClient`
- `app/agent/tool_registry.py`: tool specs, JSON Schema export, `tool_schema_hash`, defensive `execute`
- `app/agent/prompts.py`: `PROMPT_VERSION = "m2-v1"`; stable prefix ordering
- `app/agent/orchestrator.py`: the loop, budgets (steps, time, calls per step, result size, context window), and typed failures
- `app/schemas/agent.py`: the `submit_answer` contract
- Migration `0002_runs`. Alembic's `render_item` now renders `UTCDateTime` as `sa.DateTime()`, after autogenerate produced an unimportable reference for the second time.

## Manual evaluation (qwen3.5:4b, derived model with num_ctx 8192, thinking off, temperature 0, full Adult data, i7-1165G7 CPU)

| # | Question | Outcome | Steps / ops (failed) | Time | Correctness (checked against direct pandas computation) |
|---|---|---|---|---|---|
| 1 | Hours, men vs women | completed | 3 / 1 (0) | 100 s | Correct (42.43 vs 36.41; n=21,790 / 10,771) |
| 2 | Share >50K by education | completed | 4 / 3 (1) | 240 s | All 15 cited shares correct; **HS-grad omitted** (16 levels exist). Repaired a `columns`/`group_by` overlap after the error. |
| 3 | Is age related to income? | completed | 5 / 3 (1) | 246 s | Correct (mean 44.25 vs 36.78; median 44 vs 34); stated as association. Repaired `mean` requested on categorical `income`. |
| 4 | Which columns have missing values? | completed | 3 / 1 (0) | 157 s | Claims correct (1,836 / 1,843 / 583). **Explanation text wrong twice:** "14 columns" (there are 15), and calls workclass "the most missing" (occupation has more). |
| 5 | Mean hours for age > 40 (needs filtering) | **failed: CONTEXT_EXHAUSTED** at step 6 | 5 / 5 (0) | 416 s | Did not state that filtering is unsupported. Used `group_by age` (73 groups, truncated to the 30 largest), then repeated calls until the context guard stopped the run. Truth: 41.29. |

An earlier run of Q1 on the 4,096-token default model also completed correctly (82 s, 2 model calls).

## Findings
1. **The acceptance criterion is met.** Real tools are executed, and claims cite real evidence IDs. 4 of 5 runs completed with correct cited numbers.
2. **Error-feedback repair works on real model mistakes** (2 of 2 recovered in one round). The field-description fix did not prevent the overlap mistake on a different question, so prevention through descriptions is partial.
3. **The explanation text is unverified.** Claims are checked for citations, but the free-text `explanation` sits beside them and contained factual errors (Q4). The UI and evaluation must not treat the explanation as grounded. Options for M3: require every number in the explanation to also appear in a cited claim, or present the explanation as non-authoritative.
4. **Citation checks do not ensure completeness.** Q2 omitted a group without any signal. Evaluation rubrics need a completeness criterion.
5. **The missing `create_view` (filtering) is the most visible capability gap.** Instead of declining, the model approximated with high-cardinality grouping. The result truncation would have made any aggregate computed from it wrong. The context guard turned this into a loud failure rather than a wrong answer.
6. **Latency:** 100–416 s per question. Prompt tokens accumulate across steps (5k–21k per run). This works for development but is slow for a live demo. Future levers: the RTX 4070 laptop, fewer steps, and compaction (ADR-004).

## Remaining risks and follow-ups
- One run per question at temperature 0; these are not reliability estimates.
- The token estimate (chars / 3.0) overestimated the initial prompt by about 9% (1,570 vs 1,434); it is not calibrated for other models.
- Candidate prompt change: tell the model not to approximate unsupported filters with grouping. This requires a `PROMPT_VERSION` bump; best done together with `create_view`.
