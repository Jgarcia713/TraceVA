"""Background execution of agent runs for the HTTP API (202 Accepted + polling).

`submit` persists the question and a 'running' run synchronously (so the caller gets a
run ID, and caller errors surface immediately), then executes the run on a thread pool.
The database is the only shared state; the API polls it.

Single-process assumption: interrupted-run reconciliation at startup is correct only
with one server process (uvicorn's default). Multiple workers are not supported.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import LLMClient
from app.agent.orchestrator import AgentOrchestrator, PendingRun, RunFailure, RunStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.models import AnalysisRun, ChatMessage
from app.provenance.timeline import EntityType, EventType, emit

logger = logging.getLogger(__name__)


class RunManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        llm_factory: Callable[[], LLMClient],
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._llm_factory = llm_factory
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_runs, thread_name_prefix="traceva-run"
        )
        self._futures: dict[str, Future[object]] = {}

    def submit(self, question: str, conversation_id: str) -> PendingRun:
        """Start a run and schedule its execution. Raises UnknownConversationError or
        ConversationBusyError before anything executes."""
        orchestrator = AgentOrchestrator(self._sessions, self._llm_factory(), self._settings)
        pending = orchestrator.start(question, conversation_id)
        future: Future[object] = self._executor.submit(orchestrator.run, pending)
        self._futures[pending.run_id] = future
        run_id = pending.run_id

        def _forget(_future: Future[object]) -> None:
            self._futures.pop(run_id, None)

        future.add_done_callback(_forget)
        return pending

    def in_flight(self) -> list[str]:
        return list(self._futures)

    def shutdown(self, wait: bool = True) -> None:
        # Queued (not yet started) runs are cancelled and stay 'running' in the database;
        # the next startup marks them INTERRUPTED.
        self._executor.shutdown(wait=wait, cancel_futures=True)


def reconcile_interrupted_runs(session_factory: sessionmaker[Session]) -> list[str]:
    """Mark runs left 'running' by a previous process as failed (INTERRUPTED).

    Call once at startup, before accepting requests. Emits RUN_FAILED so the timeline
    shows how each run ended.
    """
    with session_factory.begin() as session:
        stale = session.scalars(
            select(AnalysisRun).where(AnalysisRun.status == RunStatus.RUNNING)
        ).all()
        for run in stale:
            run.status = RunStatus.FAILED
            run.completed_at = utcnow()
            run.error_json = {
                "code": RunFailure.INTERRUPTED,
                "message": "the server stopped before this run finished",
                "details": {},
            }
            question_id = session.scalar(
                select(ChatMessage.id).where(
                    ChatMessage.run_id == run.id, ChatMessage.role == "user"
                )
            )
            emit(
                session,
                conversation_id=run.conversation_id,
                event_type=EventType.RUN_FAILED,
                entity_type=EntityType.RUN,
                entity_id=run.id,
                run_id=run.id,
                message_id=question_id,
            )
        if stale:
            logger.warning("marked %d interrupted run(s) as failed", len(stale))
        return [run.id for run in stale]
