"""Export recorded real runs into a replay catalog for demo mode.

Writes model-visible responses (content and tool calls exactly as recorded) and each
operation's produced IDs. It contains no data rows and no reasoning text: reasoning was
never stored (ADR-004), and tool results are recomputed live during replay.

Usage (from backend/):
    python scripts/export_demo.py run_abc... run_def... --out demo/catalog.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agent.replay import CATALOG_VERSION, record_run
from app.config import get_settings
from app.db.session import create_app_engine, create_session_factory


def export(run_ids: list[str]) -> dict[str, object]:
    engine = create_app_engine(get_settings().database_url)
    try:
        with create_session_factory(engine)() as db:
            runs = [record_run(db, run_id) for run_id in run_ids]
    finally:
        engine.dispose()
    questions = [r["question"] for r in runs]
    if len(set(questions)) != len(questions):
        raise SystemExit("duplicate questions: replay matches by exact question text")
    return {"version": CATALOG_VERSION, "runs": runs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--out", default="demo/catalog.json")
    args = parser.parse_args()
    catalog = export(args.run_ids)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(catalog['runs'])} recorded runs to {out}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
