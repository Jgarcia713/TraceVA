# Pre-M2 local model smoke test (2026-11-22)

## Scope
Before designing the M2 agent loop, check whether a local model on the Iris Xe laptop can produce valid tool calls against the real tool schemas, and how thinking mode, API choice, and latency behave. Script: `backend/scripts/model_smoke.py`.

## Setup
- Hardware: i7-1165G7 (4 cores / 8 threads), 15.7 GB RAM, Intel Iris Xe (driver 27.20.100.9415, from 2021). Inference ran on the CPU.
- Ollama 0.40.17, model `qwen3.5:4b-q4_K_M` (3.3 GB). `llama3.2` (3B) is installed and is kept only as a weak baseline.
- One question ("On average, how many hours per week do men and women work?"), tool schemas generated from `InspectDatasetInput` and `SummarizeInput`, temperature 0, no tool execution.

## Results (single runs)

| Configuration | Time | Generated | Reasoning text | Tool call |
|---|---|---|---|---|
| Native `/api/chat`, `think: false` | 24.1 s (12.6 s uncached prompt) | 101 tok | none | `summarize`, invalid (`sex` in both `columns` and `group_by`) |
| Native, default (thinking) | 31.9 s | 309 tok | 778 chars | `inspect_dataset` |
| OpenAI-compatible, default | 32.1 s | 309 tok | 778 chars (`reasoning` field) | `inspect_dataset` |
| OpenAI-compatible, `reasoning_effort: "none"` | 12.4 s | 101 tok | none | same invalid `summarize` |
| Repair turn (validation error returned to model) | 17.3 s | 98 tok | none | valid and correct |
| After improving the `columns`/`group_by` field descriptions (3 repeats) | ~12.5 s each | 98 tok | none | valid and correct on the first call, 3/3 |

Throughput: about 11 tok/s generation, about 61 tok/s for uncached prompt processing, and about 4,000+ tok/s when Ollama's prompt cache hit.

## Findings that shape M2
1. **The OpenAI-compatible endpoint is sufficient.** `reasoning_effort: "none"` disables thinking (output matched native `think: false` token for token), so the `LLMClient` adapter can use the OpenAI SDK as planned.
2. **Reasoning arrives in a separate `reasoning` field when thinking is on.** The adapter must discard it and never persist it (brief invariant 8).
3. **Thinking off is the development default.** It uses about 3× fewer tokens. Thinking on chose a more cautious first step (`inspect_dataset`), so compare the two in evaluation rather than settling it here.
4. **Strict argument validation plus error feedback works.** The model repaired its call in one round. Clearer field descriptions prevented the mistake altogether in this test. Tool schema text is part of the effective prompt, so M2 run metadata must record a tool-schema version or hash.
5. **Latency budget.** Expect roughly 30–60 s per 2–3-step question on this laptop. Keep the system prompt and tool schemas byte-stable at the start of the context so that the prompt cache keeps hitting, and keep tool results compact.

## Limitations
One question, temperature 0, so repeats are not independent samples. No multi-step loop, no tool execution, no final-claim generation. `qwen3.5:9b` and the RTX 4070 laptop were not tested. These numbers inform design only and are not evaluation results.
