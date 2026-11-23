"""Background execution of agent runs for the HTTP API (202 Accepted + polling).

`submit` persists the question and a 'running' run synchronously (so the caller gets a
run ID, and caller errors surface immediately), then executes the run on a thread pool.
The database is the only shared state; the API polls it.

Single-process assumption: interrupted-run reconciliation at startup is correct only
with one server process (uvicorn's default). Multiple workers are not supported, and the
CLI (`scripts/ask.py`) should not run while the server starts: reconciliation would mark
its run INTERRUPTED (the CLI then leaves that status unchanged rather than overwrite it).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import LLMClient
from app.agent.orchestrator import AgentOrchestrator, PendingRun, RunFailure, RunStatus
from app.config import Settings
from app.db.base import utcnow
from app.db.models import AnalysisRun, ChatMessage
from app.db.session import write_transaction
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
        self._stopping = threading.Event()

    def submit(self, question: str, conversation_id: str) -> PendingRun:
        """Start a run and schedule its execution. Raises UnknownConversationError or
        ConversationBusyError before anything executes."""
        orchestrator = AgentOrchestrator(
            self._sessions,
            self._llm_factory(),
            self._settings,
            should_stop=self._stopping.is_set,
        )
        pending = orchestrator.start(question, conversation_id)
        future: Future[object] = self._executor.submit(orchestrator.run, pending)
        self._futures[pending.run_id] = future
        run_id = pending.run_id

        def _forget(done: Future[object]) -> None:
            self._futures.pop(run_id, None)
            if done.cancelled():
                return  # queued at shutdown; the next startup marks it INTERRUPTED
            exc = done.exception()
            if exc is not None:  # run() could not even record its own failure
                logger.error("run %s crashed: %r", run_id, exc)
                try:
                    fail_if_running(self._sessions, run_id, RunFailure.INTERNAL_ERROR, repr(exc))
                except Exception:
                    logger.exception("could not mark run %s as failed", run_id)

        future.add_done_callback(_forget)
        return pending

    def in_flight(self) -> list[str]:
        return list(self._futures)

    def shutdown(self, wait: bool = True) -> None:
        # Running runs stop at their next step (INTERRUPTED); a model call already in
        # progress still finishes first. Queued runs are cancelled and stay 'running';
        # the next startup marks them INTERRUPTED.
        self._stopping.set()
        self._executor.shutdown(wait=wait, cancel_futures=True)


def _mark_failed(session: Session, run: AnalysisRun, code: str, message: str) -> None:
    run.status = RunStatus.FAILED
    run.completed_at = utcnow()
    run.error_json = {"code": str(code), "message": message, "details": {}}
    question_id = session.scalar(
        select(ChatMessage.id).where(ChatMessage.run_id == run.id, ChatMessage.role == "user")
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


def fail_if_running(
    session_factory: sessionmaker[Session], run_id: str, code: str, message: str
) -> bool:
    with write_transaction(session_factory) as session:
        run = session.get(AnalysisRun, run_id)
        if run is None or run.status != RunStatus.RUNNING:
            return False
        _mark_failed(session, run, code, message)
        return True


def reconcile_interrupted_runs(session_factory: sessionmaker[Session]) -> list[str]:
    """Mark runs left 'running' by a previous process as failed (INTERRUPTED).

    Call once at startup, before accepting requests. Emits RUN_FAILED so the timeline
    shows how each run ended.
    """
    with write_transaction(session_factory) as session:
        stale = session.scalars(
            select(AnalysisRun).where(AnalysisRun.status == RunStatus.RUNNING)
        ).all()
        for run in stale:
            _mark_failed(
                session, run, RunFailure.INTERRUPTED, "the server stopped before this run finished"
            )
        if stale:
            logger.warning("marked %d interrupted run(s) as failed", len(stale))
        return [run.id for run in stale]
