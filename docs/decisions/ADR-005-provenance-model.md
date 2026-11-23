# ADR-005: Provenance model for Milestone 3

Status: Accepted
Date: 2026-11-23

## Context
M3 must persist timeline events and assumptions, expose a typed provenance graph and a chronological event list, and let a chat selection resolve to graph nodes (brief §12). The brief sketches a generic `ProvenanceEdge` table (§6). M2 left four open questions: how the graph is stored, what follow-up questions see from earlier turns, how to treat the unverified explanation text, and at what granularity the agent states caveats. A further requirement from the project owner: the design must not be built around the current local model (qwen3.5:4b), because a stronger hosted model may replace it.

## Decisions

1. **The graph is computed, not stored.** Edges are derived from relational records: foreign keys, `input_ids_json` / `output_ids_json`, `claim_evidence`, and `assumption_links`. Each edge type has a fixed origin (`backend_verified`, `agent_proposed`, `agent_stated`, `method_template`). *Alternative:* a generic `provenance_edges` table, which is more flexible but duplicates existing relationships and needs application code to enforce endpoint types. *Why:* one source of truth, and endpoint validity enforced by the schema. `assumption_links` uses typed nullable foreign keys plus a CHECK that exactly one target is set.
2. **Follow-up context: earlier questions and their claims only.** At most the last 3 turns are included; failed turns are noted with their error code. Free-text explanations are never carried over, because they are unverified. Claims must still cite evidence produced in the current run, so the model reruns tools. *Deviation from the option as first described:* earlier evidence IDs are not shown to the model, because it cannot cite them and because ID handling is this model's weakest skill (see `docs/implementation/create-view.md`). M4 widens the citation scope for challenge branches.
3. **Unverified text: the minimum check only.** The explanation is labelled unverified, and `unverified_numbers` lists numbers in it that appear in no claim (regex-based soft check). *Candidate future experiments:* checking claim numbers against cited evidence, and backend-bound values (the model references value handles and the backend inserts the numbers).
4. **Caveat granularity is hybrid and optional.**
   - `submit_answer` claims accept optional per-claim `caveats`. The answer-level `caveats` list is kept and links to the **answer message**, never fanned out to each claim, which would invent specificity the agent did not state.
   - Identical caveat text within a run is stored once, with multiple links.
   - Method-template caveats (backend-generated from a tool's own result) link to operations and reach claims by **lineage traversal**: claim → cited evidence → operation → consumed views → producing operations. This works regardless of which model is used.
   - *Model independence:* a weaker model can leave the per-claim field empty and nothing breaks. Fill rate and accuracy per model become evaluation metrics.

## Timeline design
- Each event is emitted in the same transaction as its entity. `sequence_no` is unique per conversation, and each event stores both its sequence number and its timestamp.
- `message_id` is the chat message that caused the event (the run's question), which makes "chat selection → related nodes" a direct lookup.
- Event vocabulary: `USER_MESSAGE`, `RUN_STARTED`, `TOOL_SUCCEEDED`, `TOOL_FAILED`, `VIEW_CREATED`, `EVIDENCE_CREATED`, `ASSUMPTION_RECORDED`, `CLAIM_CREATED`, `ASSISTANT_MESSAGE`, `RUN_COMPLETED`, `RUN_FAILED`.
- Deviations from the brief: `TOOL_REQUESTED` is omitted, because operations are written once after execution and model calls record the requests; `ASSISTANT_MESSAGE` and `VIEW_CREATED` are added.
- A graph snapshot at sequence N contains nodes created at or before N. Nodes created outside the conversation (sources, root views, views first created elsewhere) are always present. Temporal adjacency never becomes an edge.

## Consequences
- All assumptions have status `not_tested`. Nothing in M3 tests an assumption (brief invariant 7).
- Lineage traversal can cross runs when a view was first created in another run; this is intended, because it is the same view definition.
- New tables only (`0004_provenance`); runs recorded before this revision have no events or assumptions, and nothing is backfilled.

## Verification
- `tests/integration/test_provenance.py` and `tests/unit/test_provenance_pure.py` (157 tests in total). They cover:
  - contiguous event sequences, with every event resolving to a real entity of the declared type and to its triggering message
  - where each assumption is linked, including deduplication
  - the CHECK constraint
  - derived lineage
  - edge origins and endpoints
  - snapshots
  - follow-up context that excludes explanations
- Mutation checks: fanning answer caveats out to every claim, and disabling upstream traversal, each fail exactly the intended test.
- Real-model check: see `docs/implementation/m3a-provenance.md`.
