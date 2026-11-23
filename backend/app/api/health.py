from __future__ import annotations

from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import BACKEND_DIR
from app.schemas.api import HealthResponse

router = APIRouter(tags=["health"])


def _schema_head() -> str | None:
    return ScriptDirectory(str(BACKEND_DIR / "alembic")).get_current_head()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    revision: str | None = None
    database_ok = True
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            has_version_table = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
            ).first()
            if has_version_table:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        database_ok = False

    head = _schema_head()
    up_to_date = database_ok and revision == head
    return HealthResponse(
        status="ok" if up_to_date else "degraded",
        database="ok" if database_ok else "error",
        schema_revision=revision,
        schema_head=head,
        schema_up_to_date=up_to_date,
    )
