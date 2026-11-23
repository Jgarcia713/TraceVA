"""Summarize recorded agent runs from the provenance database (read-only).

Every number comes from persisted records: runs, model calls, operations, and claims.
That is itself a use of the provenance store. Correctness is NOT computed here; it
requires reference answers (brief §10A) and is reported separately.

Usage (from backend/):
    python scripts/report_runs.py                 # markdown to stdout
    python scripts/report_runs.py --csv runs.csv  # also write one row per run
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import AnalysisRun, ChatMessage, Claim, ModelCall, Operation
from app.db.session import create_app_engine, create_session_factory


def collect() -> list[dict[str, Any]]:
    settings = get_settings()
    engine = create_app_engine(settings.database_url)
    rows: list[dict[str, Any]] = []
    try:
        with create_session_factory(engine)() as db:
            for run in db.scalars(select(AnalysisRun).order_by(AnalysisRun.created_at)):
                ops = db.scalars(
                    select(Operation)
                    .where(Operation.run_id == run.id)
                    .order_by(Operation.sequence_no)
                ).all()
                calls = db.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all()
                failed = [o for o in ops if o.status == "failed"]
                # A failure counts as repaired if a later call of the same tool succeeded.
                repaired = sum(
                    1
                    for f in failed
                    if any(
                        o.tool_name == f.tool_name
                        and o.status == "succeeded"
                        and o.sequence_no > f.sequence_no
                        for o in ops
                    )
                )
                usage = run.usage_json or {}
                question = db.scalar(
                    select(ChatMessage.content).where(
                        ChatMessage.run_id == run.id, ChatMessage.role == "user"
                    )
                )
                rows.append(
                    {
                        "run_id": run.id,
                        "created_at": run.created_at.isoformat(timespec="seconds"),
                        "question": question or "",
                        "model": run.config_json.get("llm", {}).get("model"),
                        "provider": run.config_json.get("llm", {}).get("provider"),
                        "prompt_version": run.config_json.get("prompt_version"),
                        "status": run.status,
                        "failure": (run.error_json or {}).get("code"),
                        "model_calls": len(calls),
                        "operations": len(ops),
                        "failed_operations": len(failed),
                        "repaired_failures": repaired,
                        "failure_codes": ";".join(
                            str((o.error_json or {}).get("code")) for o in failed
                        ),
                        "claims": len(
                            db.scalars(select(Claim).where(Claim.run_id == run.id)).all()
                        ),
                        "wall_s": round(usage.get("wall_ms", 0) / 1000, 1),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "reasoning_chars_discarded": usage.get("reasoning_chars_discarded"),
                    }
                )
    finally:
        engine.dispose()
    return rows


def _cell(text: str) -> str:
    """Markdown table cell: escape pipes and flatten newlines."""
    return " ".join(text.split()).replace("|", r"\|")


def markdown(rows: list[dict[str, Any]]) -> str:
    real = [r for r in rows if r["provider"] not in ("fake", "replay")]
    out = [f"Runs: {len(real)} (excluding fake/replay runs)", ""]
    out.append(
        "| # | Prompt | Status | Calls | Ops (failed, repaired) | Failure codes "
        "| Claims | Wall s | Question |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(real, 1):
        status = r["status"] + (f" ({r['failure']})" if r["failure"] else "")
        out.append(
            f"| {i} | {r['prompt_version']} | {status} | {r['model_calls']} | "
            f"{r['operations']} ({r['failed_operations']}, {r['repaired_failures']}) | "
            f"{r['failure_codes'] or '-'} | {r['claims']} | {r['wall_s']} | "
            f"{_cell(r['question'][:60])} |"
        )
    if not real:
        return "\n".join(out)
    completed = [r for r in real if r["status"] == "completed"]
    walls = [r["wall_s"] for r in real]
    codes = Counter(c for r in real for c in r["failure_codes"].split(";") if c)
    failed_total = sum(r["failed_operations"] for r in real)
    repaired_total = sum(r["repaired_failures"] for r in real)
    out += [
        "",
        f"- Completed runs: {len(completed)}/{len(real)}; run failures: "
        f"{dict(Counter(r['failure'] for r in real if r['failure']))}",
        f"- Operations: {sum(r['operations'] for r in real)} total, {failed_total} failed, "
        f"{repaired_total} of those followed by a successful call of the same tool",
        f"- Operation failure codes: {dict(codes)}",
        f"- Wall time per run: median {statistics.median(walls):.0f} s, "
        f"min {min(walls):.0f} s, max {max(walls):.0f} s",
        f"- Model calls per run: median {statistics.median(r['model_calls'] for r in real)}",
        "- Prompt tokens per run: median "
        + (
            f"{statistics.median(tokens):.0f} (over {len(tokens)} runs with token data)"
            if (tokens := [r["prompt_tokens"] for r in real if r["prompt_tokens"] is not None])
            else "n/a"
        ),
        "- Reasoning characters discarded: "
        f"{sum(r['reasoning_chars_discarded'] or 0 for r in real)}",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", help="also write per-run rows to this CSV file")
    args = parser.parse_args()
    rows = collect()
    print(markdown(rows))
    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
