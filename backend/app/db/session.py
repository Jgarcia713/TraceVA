"""Engine construction for the app and for migrations.

Two pysqlite behaviours matter here (ADR-002):

1. `PRAGMA foreign_keys` is per-connection and a no-op inside a transaction, so it is
   set in a `connect` listener on each engine *instance* (never the global Engine class).
2. The stdlib sqlite3 driver, in its legacy transaction mode, does not emit BEGIN before
   DDL or SELECT. DDL would then autocommit statement by statement, and a failed migration
   could not be rolled back. We disable the driver's implicit transaction handling and
   emit BEGIN ourselves (the recipe from the SQLAlchemy SQLite dialect docs), making
   transactions, including DDL, behave as written.

The app engine also enables WAL journaling, so API reads are not blocked while a
background run writes, and a busy timeout, so brief write contention waits instead of
failing immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

BUSY_TIMEOUT_MS = 5000
IMMEDIATE_OPTION = "traceva_begin_immediate"


def _configure_sqlite(engine: Engine, *, foreign_keys: bool, wal: bool = False) -> None:
    pragma = "ON" if foreign_keys else "OFF"

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.isolation_level = None  # driver autocommit; SQLAlchemy owns BEGIN
        cursor = dbapi_conn.cursor()
        cursor.execute(f"PRAGMA foreign_keys={pragma}")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if wal:
            cursor.execute("PRAGMA journal_mode=WAL")  # persistent per database file
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        # Write transactions take the write lock up front (BEGIN IMMEDIATE): in WAL mode a
        # deferred transaction that reads, then writes after another commit, fails at once
        # with SQLITE_BUSY_SNAPSHOT, which busy_timeout cannot retry.
        immediate = conn.get_execution_options().get(IMMEDIATE_OPTION)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def create_app_engine(database_url: str) -> Engine:
    """Engine for application use: foreign keys enforced on every connection."""
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    _configure_sqlite(engine, foreign_keys=True, wal=True)
    return engine


def create_migration_engine(database_url: str) -> Engine:
    """Engine for Alembic: FK enforcement off during table rebuilds, transactional DDL.

    Callers must run `assert_no_fk_violations` before committing (see alembic/env.py).
    """
    engine = create_engine(database_url, poolclass=NullPool)
    _configure_sqlite(engine, foreign_keys=False)
    return engine


class ForeignKeyViolationError(RuntimeError):
    pass


def assert_no_fk_violations(conn: Connection) -> None:
    rows = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if rows:
        raise ForeignKeyViolationError(
            f"{len(rows)} foreign-key violation(s) after migration; first: {rows[:5]}"
        )


@contextmanager
def write_transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session in one write transaction started with BEGIN IMMEDIATE.

    Use for every transaction that writes; plain `factory()` sessions stay deferred so
    reads never take the write lock.
    """
    with factory.begin() as session:
        session.connection(execution_options={IMMEDIATE_OPTION: True})
        yield session


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
