"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.agent.runner import RunManager


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_run_manager(request: Request) -> RunManager:
    manager: RunManager = request.app.state.run_manager
    return manager


SessionDep = Annotated[Session, Depends(get_session)]
RunManagerDep = Annotated[RunManager, Depends(get_run_manager)]
