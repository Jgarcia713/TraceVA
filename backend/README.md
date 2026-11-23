# TraceVA backend

FastAPI + SQLite backend for TraceVA. The design brief is `../TRACEVA_BACKEND_CONTEXT.md`; decisions are in `../docs/decisions/`.

## Setup (Windows PowerShell; POSIX equivalents in comments)

```powershell
cd backend
py -3.12 -m venv .venv                 # python3 -m venv .venv
.venv\Scripts\activate                 # source .venv/bin/activate
python -m pip install -e ".[dev]"
copy .env.example .env                 # cp .env.example .env  (optional; defaults work)
alembic upgrade head                   # creates backend/traceva.db
```

## Run

```powershell
uvicorn app.main:create_app --factory --reload
curl http://127.0.0.1:8000/api/v1/health
```

`/health` reports `"status": "degraded"` if the database is unreachable or not migrated to the latest Alembic revision.

## Checks

```powershell
pytest                 # no dataset download or model access required
ruff check .
ruff format --check .
mypy app
```

## Schema changes

Every model change needs a reviewed migration (ADR-002):

```powershell
alembic revision --autogenerate -m "describe change"   # then review/edit the draft
alembic upgrade head
alembic check                                           # fails if models and migrations drift
```

Migrations run with foreign keys off inside one transaction and are rolled back if `PRAGMA foreign_key_check` finds violations. Never backfill provenance values (e.g. `origin`) that were not actually observed.

## Datasets and tools (Milestone 1)

```powershell
python scripts/register_datasets.py adult   # verifies SHA-256, creates source + root view
curl http://127.0.0.1:8000/api/v1/datasets
```

Raw files must be placed under `../datasets/` first (see `../datasets/README.md`). Analytical tools are plain Python functions (`app/analysis/tools.py`) and are not yet exposed over HTTP:

```python
from app.analysis.tools import ToolContext, summarize
from app.schemas.tools import SummarizeInput
out = summarize(ctx, SummarizeInput(view_id=..., columns=["hours_per_week"],
                                    group_by=["sex"], metrics=["mean", "median"]))
```
