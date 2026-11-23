"""Register known datasets into the configured database (idempotent).

Usage (from backend/, after `alembic upgrade head`):
    python scripts/register_datasets.py            # registers "adult"
    python scripts/register_datasets.py adult ...
"""

from __future__ import annotations

import argparse
import sys

from app.analysis.datasets import DatasetIntegrityError
from app.analysis.registry import register_dataset, registrable_datasets, root_view_id
from app.config import get_settings
from app.db.session import create_app_engine, create_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", default=["adult"], choices=registrable_datasets())
    args = parser.parse_args()

    settings = get_settings()
    engine = create_app_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    status = 0
    try:
        for name in args.names:
            try:
                with session_factory.begin() as session:
                    source = register_dataset(session, name, settings.data_dir)
                    print(
                        f"{name}: source_id={source.id} root_view_id="
                        f"{root_view_id(session, source.id)} rows={source.row_count} "
                        f"sha256={source.sha256[:12]}..."
                    )
            except DatasetIntegrityError as exc:
                print(f"{name}: FAILED: {exc}", file=sys.stderr)
                status = 1
    finally:
        engine.dispose()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
