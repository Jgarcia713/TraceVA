# ADR-002: Schema migrations with Alembic on SQLite

Status: Accepted
Date: 2026-11-22

## Context
SQLite is the authoritative store for analytical provenance (brief §5). The brief requires foreign-key enforcement, append-only provenance records, and explicit schema versions (§6). From roughly Milestone 4 on, the database will hold data that cannot be regenerated: nondeterministic model runs, challenge branches, and benchmark trials. The schema will grow across milestones (provenance tables in M3, revisions in M4).

SQLite supports very limited `ALTER TABLE`: no column type changes and no dropping of constraints. Alembic works around this with "batch mode", which recreates the table (create new → copy rows → drop old → rename). That interacts dangerously with FK enforcement:

- `PRAGMA foreign_keys` is **per connection** and not persisted.
- The pragma is a **no-op inside a transaction**.
- `DROP TABLE` with enforcement on performs an implicit `DELETE FROM`, which fires `ON DELETE` actions. It can silently delete child rows via cascades or abort the migration.
- `PRAGMA defer_foreign_keys` only defers violation *checks*, not cascade *actions*, so it is insufficient.

## Options
1. **Alembic from Milestone 0**, configured for SQLite.
2. **`metadata.create_all`** until ~M3, then add an Alembic baseline and `alembic stamp` the existing database.

## Decision
Use **Alembic from Milestone 0**, with:

1. **Constraint naming convention** on the declarative base's `MetaData(naming_convention=...)`, so that batch mode can address constraints by name.
2. **`render_as_batch=True`** in `alembic/env.py`.
3. **SQLite's own procedure for table rebuilds** (sqlite.org/lang_altertable.html, "Making Other Kinds Of Table Schema Changes"), implemented in `env.py`:
   - run `PRAGMA foreign_keys=OFF` on the migration connection *before* any transaction begins
   - run the migrations inside one transaction
   - run `PRAGMA foreign_key_check` before commit; any violation raises an error and rolls back the migration
4. **App engine enables FKs per connection** through a `connect` event listener attached to the **app engine instance**, not the global `Engine` class, so that Alembic's engine does not inherit enforcement.
5. **No `ON DELETE CASCADE`** on provenance foreign keys (`RESTRICT`/default no action). This is consistent with append-only invariants and turns any accidental drop with FKs on into a loud failure instead of silent data loss.

## Rationale
- Setup is cheap now, before any data exists. Retrofitting a baseline onto a populated database near M3 coincides with the riskiest schema growth.
- Versioned migrations document how the provenance store's own schema evolved, satisfying "explicit schema versions".
- Migrations force explicit decisions about backfilling historical rows. Example: adding a NOT NULL `ProvenanceEdge.origin` must not backfill `backend_verified` onto edges that were never classified, because that would fabricate provenance. Prefer a nullable column or an explicit sentinel (e.g. `unknown_pre_vN`).
- Disabling enforcement during rebuilds and then running `foreign_key_check` before commit means the relaxation never persists into committed state.

## Consequences
- Every model change needs a reviewed migration script. Autogenerate output is a draft: it does not detect renames (it emits drop + add, which loses data) and misses some type and default changes.
- Data migrations that touch historical provenance require explicit justification in the migration docstring.
- Tests should build schema via `alembic upgrade head` (not `create_all`), so the migration path itself is exercised.

## Implementation notes (as built, 2026-11-22)
- **pysqlite does not make DDL transactional by default.** In its legacy transaction mode, the stdlib `sqlite3` driver emits no `BEGIN` before DDL, so `CREATE`/`DROP` autocommit statement by statement, and rolling back after a failed `foreign_key_check` would not undo them. We verified this empirically: with a plain engine, a table created inside a rolled-back transaction survived. Both engines (`backend/app/db/session.py`) therefore set `isolation_level = None` on the DBAPI connection and emit `BEGIN` from SQLAlchemy's `begin` event, following the SQLite dialect documentation's recipe. Without this, piece 3 of the decision would silently not hold.
- `PRAGMA foreign_keys` is set in the same `connect` listener, before any `BEGIN`, so it takes effect: ON for the app engine, OFF for the migration engine.
- Revisions: `0000_baseline` (empty; establishes version tracking) and `0001_datasets`. Autogenerate output referenced `app.db.base.UTCDateTime` without importing it. The migration was rewritten to use plain `sa.DateTime()`, and migrations do not import app types.
- `dataset_views` has a partial unique index (`source_id WHERE parent_view_id IS NULL`), so each source has exactly one root view, enforced by the database.

- 2026-11-23 (M3b): the app engine also sets `PRAGMA journal_mode=WAL` and `busy_timeout=5000` in the same `connect` listener, so API reads are not blocked by a background run's writes. WAL is persistent per database file; the `-wal` and `-shm` files are gitignored.

## Verification
- `tests/integration/test_migrations.py` (passing):
  - `upgrade head → downgrade base → upgrade head` round trip
  - `alembic check` reports no drift between models and migrations
  - the app engine reports `foreign_keys = 1` and the migration engine reports `0`
  - DDL inside a failed transaction is rolled back
  - an orphan row inserted with FKs off is caught by `assert_no_fk_violations` and rolled back
- `tests/integration/test_registry_and_tools.py`: the database rejects a dangling `source_id` and a second root view (passing).
- Done (2026-11-23, `test_table_rebuild_preserves_child_rows`): the first migration that needs a real table rebuild is the downgrade of `0003_views`, which drops a column from `operations` (referenced by `evidence`). The test seeds parent and child rows, runs the downgrade, and asserts that the child rows survive, `foreign_key_check` is empty, and the column is gone. A mutation check confirmed that with FKs left on, the rebuild fails with `FOREIGN KEY constraint failed`.
