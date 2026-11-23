# TraceVA: interim progress metrics (2026-11-23)

These numbers come from development and validation runs, not from the planned experiments (M5). They describe what was built, what was checked, and which observations changed the design. Unless stated otherwise, real-model numbers come from the provenance database itself (`backend/scripts/report_runs.py`).

**Setup for all real-model runs:**
- Model: qwen3.5:4b (q4_K_M) via Ollama 0.40, thinking off, temperature 0, 8k context (the first run used the 4k default).
- Hardware: laptop CPU only (Intel i7-1165G7, 16 GB RAM).
- Data: UCI Adult Income, `adult.data`, 32,561 rows, SHA-256 pinned.

## 1. Headline numbers

| | |
|---|---|
| Automated tests (no model access needed) | **189 passing** |
| Mutation checks (deliberately broken code → the intended test fails) | **9 of 9 caught** |
| Independent code reviews | 2, with 20 findings (+2 minor), all fixed with regression tests |
| Real-model questions run end to end | **14** (3 prompt versions) |
| Runs completing with a cited answer | **11 / 14** (79%) |
| Numbers in claims of completed runs matching an independent pandas computation | **all claims of 11/11 completed runs** (21 claims) |
| Failed tool calls later followed by a successful call of the same tool | **14 / 14** |
| Share of run time spent in the model (vs. tools + provenance recording) | **99.9%** (median) |
| Median time per question (CPU laptop) | **162 s** (range 82–413 s) |
| Replay validation: recorded runs re-executed live on a fresh database | **8 / 8** completed, identical claim texts |

## 2. Engineering validation

**Automated tests (189):**
- statistics on hand-built frames with hand-computed expectations
- tool outputs checked against a stdlib-only reading of the raw file (not pandas against pandas)
- migrations, including a real table rebuild with child rows
- the agent loop with a scripted fake model
- provenance integrity: every timeline event resolves to a real entity, and every graph edge has valid endpoints
- the HTTP API with real background threads
- demo replay

**Mutation checks.** Each property below was deliberately broken to confirm a test fails:

1. Citation validation removed (claims citing evidence from another run)
2. A write transaction held open during a model call
3. Model reasoning text leaking into stored records
4. Foreign keys left on during a migration's table rebuild
5. Null keys (the missing-value group) stripped from tool results
6. Answer-level caveats fanned out to every claim
7. Upstream lineage traversal disabled
8. Concurrent identical-view creation not resolved
9. `BEGIN IMMEDIATE` removed from write transactions

**Defects caught before they reached the main branch** (selection):

| Defect | How it was caught | Consequence if missed |
|---|---|---|
| The SQLite driver autocommits DDL, so migration rollbacks silently did nothing | empirical check of a design assumption | A failed migration could leave a half-applied schema |
| pandas fills short or empty fields with `""`, creating a fake category | parser test | Silent data corruption |
| Pydantic coerced `true` into `1` in filter values | unit test | A bad filter silently becomes a different, valid filter |
| The missing-value group reached the model as `{}` | code review | The model cannot identify the "unknown" group |
| Ollama's default 4,096-token context silently drops the oldest messages | `ollama ps` before evaluation | The system prompt is lost mid-run without any error |
| Parallel runs could fail with "database is locked" | code review | Spurious run failures under the HTTP API |

## 3. Real-model runs (all 14)

| # | Prompt | Outcome | Model calls | Tool calls (failed) | Failure codes | Time | Question |
|---|---|---|---|---|---|---|---|
| 1 | m2-v1 | completed | 2 | 1 (0) | – | 82 s | Hours, men vs women (4k context) |
| 2 | m2-v1 | completed | 3 | 1 (0) | – | 100 s | Hours, men vs women |
| 3 | m2-v1 | completed | 4 | 3 (1) | INVALID_ARGUMENTS | 237 s | Share >50K by education |
| 4 | m2-v1 | completed | 5 | 3 (1) | METRIC_NOT_APPLICABLE | 243 s | Is age related to income? |
| 5 | m2-v1 | completed | 3 | 1 (0) | – | 154 s | Which columns have missing values? |
| 6 | m2-v1 | **failed: CONTEXT_EXHAUSTED** | 5 | 5 (0) | – | 413 s | Mean hours, age > 40 (no filtering tool yet) |
| 7 | m2-v1 | completed | 3 | 2 (0) | – | 196 s | Mean age by workclass, incl. unknown |
| 8 | m2-v2 | completed | 3 | 2 (0) | – | 128 s | Mean hours, age > 40 (after `create_view`) |
| 9 | m2-v2 | **failed: BUDGET_EXHAUSTED** | 6 | 6 (4) | UNKNOWN_VIEW ×4 | 165 s | Women with Bachelors: share >50K |
| 10 | m2-v2 | **failed: BUDGET_EXHAUSTED** | 6 | 6 (3) | UNKNOWN_VIEW ×3 | 139 s | same question |
| 11 | m2-v2 | completed | 6 | 5 (3) | UNKNOWN_VIEW ×3 | 199 s | same question (step budget 6 → 8) |
| 12 | m3-v1 | completed | 4 | 5 (1) | UNKNOWN_VIEW | 230 s | Hours, men vs women |
| 13 | m3-v1 | completed | 6 | 5 (0) | – | 158 s | Follow-up: "And only people older than 40?" |
| 14 | m3-v1 | completed | 5 | 3 (1) | UNKNOWN_VIEW | 153 s | Share >50K, age > 40 |

**Aggregates:**
- Tool calls: 48 in total, 14 failed (29%). Of the failures, 12 were invented view IDs (`UNKNOWN_VIEW`), 1 was an invalid argument, and 1 applied a metric to the wrong column type. Every failure was followed by a successful call of the same tool.
- Per run: median 4.5 model calls and about 13,500 prompt tokens. Generation ran at about 4.8 output tokens/s including prompt processing.
- 0 reasoning characters were discarded, because thinking was off. With thinking on, the smoke test produced 778 characters that would have had to be discarded.

**Correctness (manual, against independent pandas computation):**
- Every number in the claims of the 11 completed runs is correct. Examples: 42.43 vs 36.41 hours; 41.29 hours for age > 40; 20.9% of 1,619; 35.4% of 13,443.
- Quality issues the citation check does **not** catch:
  - **1 incomplete claim:** the education answer listed 15 of 16 levels, omitting HS-grad.
  - **2 factual errors in the free-text explanation** of run 5 ("14 columns" instead of 15, and the wrong column named as having the most missing values). The claims themselves were correct.
  - **2 claims contained differences the model computed itself** ("6.02", "5.42"). The arithmetic was correct, but the numbers do not appear in any evidence.

## 4. Observations that shaped the design

| Observation | Measurement | Design response |
|---|---|---|
| Thinking mode is expensive on a CPU | 309 vs 101 output tokens, 31.9 s vs 12.4 s per call (smoke test) | Thinking off by default; any reasoning text is discarded, never stored |
| The context window silently truncates | Ollama default: 4,096 tokens; one call ≈ 1,400 prompt tokens and growing per step | Context pinned in the model tag (8k), plus a `CONTEXT_EXHAUSTED` guard (estimator overshoots by about 9%, the safe direction) |
| The model approximated a filter with grouping | Run 6: grouped by age (73 groups, truncated to 30), then the context was exhausted | Added the `create_view` tool; the same question then succeeded (run 8, correct) |
| Invented IDs are the dominant failure | 12 of 14 failed calls; 2 runs ran out of steps | Step budget 6 → 8 (run 11 completed); errors now list valid IDs; short run-scoped aliases are a candidate |
| Clear error feedback enables repair | 14/14 failures followed by success; field-description fix: first-call validity 3/3 (one question, temperature 0) | Strict validation kept (reject, don't silently correct), with actionable error messages |
| Explanations contain unverified errors | 2 errors in 1 of the runs inspected | Explanation labelled unverified; a soft check lists numbers that appear in no claim |
| Claims can be correct but incomplete | 1 of 16 categories omitted | The chart shows the full evidence (HS-grad visible); completeness will be an evaluation criterion |
| A weak model fills no per-claim caveats | 0 per-claim caveats across the m3-v1 runs | Hybrid caveat schema; fill rate becomes a per-model metric; backend method caveats reach claims through lineage |
| Provenance recording is cheap | Model time is 99.9% of wall time; tools take 0.01–0.14 s | Record everything (operations, evidence, events) without performance concern |

## 5. Demo validation (replay mode)

- 8 recorded real runs are replayed with **live** tool execution, validation, evidence, provenance, and charts. Only the model's responses are replayed.
- **8 of 8 completed** on a freshly created database, with claim texts identical to the originals, and the original mistakes (invented IDs, bad arguments) were re-validated and rejected live.
- A replay takes 2.6–3.8 s (with a 0.6 s per-step display delay), versus 128–244 s originally.

## 6. What these numbers are not

- They are not evaluation results. Questions were chosen during development, and each configuration ran once at temperature 0, so repeats are not independent samples.
- Correctness was checked manually; automated reference answers are M5.
- There is no comparison with other models yet (qwen3.5:9b, the RTX 4070 laptop, or a hosted model).
- There is no user study; no claims are made about human performance (brief §2).

## 7. Scope changes relative to the plan

- **Execution records moved from M3 to M2**, because real evidence IDs were needed for M2's acceptance criterion.
- **Command line before HTTP:** the agent was first validated through a CLI; the HTTP API followed in M3.
- **`create_view` was added between M2 and M3**, motivated by run 6.
- **A frontend prototype and a replay demo mode were built before M4 (challenges)** to support this interim presentation. Model responses are replayed from recorded real runs; all computation is live.
