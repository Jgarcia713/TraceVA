"""Alembic environment implementing ADR-002 for SQLite.

Procedure (mirrors sqlite.org "Making Other Kinds Of Table Schema Changes"):
  1. Connection opens with PRAGMA foreign_keys=OFF (set before any transaction).
  2. All migrations run inside one real transaction (transactional DDL).
  3. PRAGMA foreign_key_check runs before commit; any violation rolls everything back.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

import app.db.models  # noqa: F401  (registers tables on Base.metadata)
from app.config import get_settings
from app.db.base import Base
from app.db.session import assert_no_fk_violations, create_migration_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_migration_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                transaction_per_migration=False,
            )
            with context.begin_transaction():
                context.run_migrations()
                assert_no_fk_violations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
