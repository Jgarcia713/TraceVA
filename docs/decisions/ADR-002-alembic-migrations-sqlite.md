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

## Verification (planned)
- M0: test `upgrade head → downgrade base → upgrade head` on a temporary database; CI/local check runs `alembic check` (Alembic ≥ 1.9) to detect model/migration drift.
- M0: test that app-engine connections report `PRAGMA foreign_keys = 1`.
- First real batch migration (expected M3): regression test that seeds parent/child rows, runs the migration, and asserts child rows survive and `foreign_key_check` is empty.
