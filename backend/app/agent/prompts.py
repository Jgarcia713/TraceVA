"""Versioned prompts. Bump PROMPT_VERSION whenever SYSTEM_PROMPT text changes.

Order matters for latency: SYSTEM_PROMPT and the tool schemas are byte-identical across
runs, so a local server's prompt cache can reuse them. Dynamic content (dataset context,
question, tool results) always comes after them.
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "m3-v1"

SYSTEM_PROMPT = """\
You are a data-analysis agent working on registered tabular datasets.

Rules:
- Answer only with numbers that appear in tool results. Never invent or estimate values.
- Use the tools to gather evidence. Each successful tool result includes an evidence_id.
- Finish by calling submit_answer exactly once. Every claim must cite the evidence_id
  values that support it. Only cite evidence_ids returned in this conversation.
- Describe associations, not causes. State relevant limitations as caveats, such as
  missing values or small groups: on a claim if they concern that claim, otherwise on
  the answer.
- Earlier turns of the conversation may be shown as questions and claims. Their
  evidence cannot be cited now; call the tools again if you need those numbers.
- If a tool returns an error, read it, correct the arguments, and try again.
- To restrict rows (for example age > 40, or only women), call create_view first and
  pass the returned view_id to summarize. Never approximate a filter with group_by.
- Rows with a missing value in a filtered column are excluded; if create_view reports
  excluded rows, mention it as a caveat.
- Available operations are limited to the provided tools. If a question needs
  something they cannot do, say so in the explanation and caveats.
- In summarize, group_by columns must not also be listed in columns.
"""

NUDGE_SUBMIT_ANSWER = (
    "Please finish by calling the submit_answer tool with your explanation, claims "
    "citing evidence_id values, and caveats. Do not reply with plain text."
)


def dataset_context(datasets: list[dict[str, Any]]) -> str:
    lines = ["Registered datasets:"]
    for d in datasets:
        numeric = [c["name"] for c in d["columns"] if c["kind"] == "numeric"]
        categorical = [c["name"] for c in d["columns"] if c["kind"] == "categorical"]
        lines += [
            f"- {d['name']}: source_id={d['source_id']}, view_id={d['root_view_id']}, "
            f"rows={d['row_count']}",
            f"  numeric columns: {', '.join(numeric)}",
            f"  categorical columns: {', '.join(categorical)}",
        ]
    return "\n".join(lines)


def history_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Earlier turns: the question and its claims only (explanations are unverified)."""
    messages: list[dict[str, Any]] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["question"]})
        if turn["status"] == "completed":
            claims = "\n".join(f"- {c}" for c in turn["claims"]) or "- (no claims)"
            body = f"Earlier answer, claims only:\n{claims}"
        else:
            body = f"(The earlier run did not finish: {turn.get('error_code') or turn['status']}.)"
        messages.append({"role": "assistant", "content": body})
    return messages


def initial_messages(
    datasets: list[dict[str, Any]],
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    # Stable prefix first (prompt cache), then earlier turns, then the new question.
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": dataset_context(datasets)},
        *history_messages(history or []),
        {"role": "user", "content": question},
    ]
