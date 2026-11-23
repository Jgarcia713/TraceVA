"""Demo replay: recorded model responses, live re-execution, ID remapping, demo API."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import FakeLLMClient, ModelResponse, ScriptStep, tool_call
from app.agent.orchestrator import AgentOrchestrator, RunStatus
from app.agent.replay import (
    CATALOG_VERSION,
    RecordedRun,
    ReplayLLMClient,
    UnknownDemoQuestionError,
    record_run,
)
from app.config import Settings
from app.db.models import AnalysisRun, Claim, ClaimEvidence, Evidence, Operation
from app.main import create_app

API = "/api/v1"
QUESTION = "Hours for people over 40?"
Messages = Sequence[dict[str, Any]]


def calls(*tc: Any) -> ModelResponse:
    return ModelResponse(tool_calls=list(tc))


def payloads(messages: Messages) -> list[dict[str, Any]]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def original_script(view_id: str) -> list[ScriptStep]:
    """A 'real' run: a failed call, a view, a summary, and a cited answer."""

    def summarize(messages: Messages) -> ModelResponse:
        new_view = payloads(messages)[-1]["result"]["view_id"]
        return calls(
            tool_call(
                "summarize",
                {"view_id": new_view, "columns": ["hours_per_week"], "metrics": ["mean"]},
            )
        )

    def answer(messages: Messages) -> ModelResponse:
        ids = [p["evidence_id"] for p in payloads(messages) if "evidence_id" in p]
        return calls(
            tool_call(
                "submit_answer",
                {
                    "explanation": "x",
                    "claims": [{"text": "Over-40s average N hours.", "evidence_ids": ids}],
                },
            )
        )

    return [
        calls(
            tool_call(
                "create_view",
                {
                    "view_id": "view_invented",
                    "filters": [{"column": "age", "op": "gt", "value": 40}],
                },
            )
        ),
        calls(
            tool_call(
                "create_view",
                {"view_id": view_id, "filters": [{"column": "age", "op": "gt", "value": 40}]},
            )
        ),
        summarize,
        answer,
    ]


@pytest.fixture
def recorded(
    session_factory: sessionmaker[Session], settings: Settings, sample_ids: dict[str, str]
) -> RecordedRun:
    result = AgentOrchestrator(
        session_factory, FakeLLMClient(original_script(sample_ids["view_id"])), settings
    ).ask(QUESTION)
    assert result.status == RunStatus.COMPLETED
    with session_factory() as db:
        return RecordedRun(**record_run(db, result.run_id))


def test_replay_reexecutes_live_and_remaps_ids(
    session_factory: sessionmaker[Session], settings: Settings, recorded: RecordedRun
) -> None:
    replay = ReplayLLMClient({QUESTION: recorded})
    result = AgentOrchestrator(session_factory, replay, settings).ask(QUESTION)
    assert result.status == RunStatus.COMPLETED

    with session_factory() as db:
        run = db.get(AnalysisRun, result.run_id)
        assert run is not None
        assert run.config_json["llm"]["provider"] == "replay"
        assert run.config_json["llm"]["replayed_run_id"] == recorded.source_run_id
        ops = db.scalars(
            select(Operation).where(Operation.run_id == run.id).order_by(Operation.sequence_no)
        ).all()
        # The original model's mistake is re-validated live, not skipped.
        assert [o.status for o in ops] == ["failed", "succeeded", "succeeded"]
        (claim,) = db.scalars(select(Claim).where(Claim.run_id == run.id)).all()
        cited = set(
            db.scalars(select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == claim.id))
        )
        live = set(
            db.scalars(select(Evidence.id).join(Operation).where(Operation.run_id == run.id))
        )
        original = {o["evidence_id"] for o in recorded.op_ids if o["evidence_id"]}
    assert cited == live and not (cited & original)  # cites the new evidence only


def test_dataset_ids_are_mapped_by_name(
    session_factory: sessionmaker[Session],
    settings: Settings,
    recorded: RecordedRun,
    sample_ids: dict[str, str],
) -> None:
    """A catalog recorded on another database uses other dataset IDs."""
    old_view = "view_" + "0" * 32
    steps = json.loads(json.dumps(recorded.steps).replace(sample_ids["view_id"], old_view))
    foreign = RecordedRun(
        **{
            **recorded.__dict__,
            "steps": steps,
            "datasets": [
                {"name": "adult_sample", "source_id": "src_" + "0" * 32, "root_view_id": old_view}
            ],
        }
    )
    result = AgentOrchestrator(session_factory, ReplayLLMClient({QUESTION: foreign}), settings).ask(
        QUESTION
    )
    assert result.status == RunStatus.COMPLETED


def test_unknown_question_is_rejected_up_front(
    session_factory: sessionmaker[Session], settings: Settings, recorded: RecordedRun
) -> None:
    with pytest.raises(UnknownDemoQuestionError):
        AgentOrchestrator(session_factory, ReplayLLMClient({QUESTION: recorded}), settings).start(
            "Something else?"
        )


def test_demo_api_end_to_end(
    session_factory: sessionmaker[Session],
    settings: Settings,
    recorded: RecordedRun,
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"version": CATALOG_VERSION, "runs": [recorded.__dict__]}), encoding="utf-8"
    )
    demo = settings.model_copy(
        update={"llm_provider": "replay", "demo_catalog_path": catalog, "demo_step_delay_s": 0.0}
    )
    with TestClient(create_app(demo)) as client:
        info = client.get(f"{API}/demo").json()
        assert info["enabled"] and info["notice"]
        assert [q["question"] for q in info["questions"]] == [QUESTION]

        conv = client.post(f"{API}/conversations").json()["id"]
        unknown = client.post(
            f"{API}/conversations/{conv}/messages", json={"content": "Not recorded?"}
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "DEMO_UNKNOWN_QUESTION"

        run_id = client.post(
            f"{API}/conversations/{conv}/messages", json={"content": QUESTION}
        ).json()["run_id"]
        deadline = time.monotonic() + 15
        while (run := client.get(f"{API}/runs/{run_id}").json())["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert run["status"] == "completed"

        claim = run["claims"][0]
        charts = client.get(f"{API}/claims/{claim['id']}/charts").json()["charts"]
        assert charts and charts[0]["chart_type"] == "bar"
        summary_ev = next(
            o["evidence_id"] for o in run["operations"] if o["tool_name"] == "summarize"
        )
        assert client.get(f"{API}/evidence/{summary_ev}/chart").status_code == 200
        view_ev = next(
            o["evidence_id"]
            for o in run["operations"]
            if o["tool_name"] == "create_view" and o["evidence_id"]
        )
        no_chart = client.get(f"{API}/evidence/{view_ev}/chart")
        assert no_chart.status_code == 404 and no_chart.json()["error"]["code"] == "NO_CHART"


def test_demo_disabled_by_default() -> None:
    from app.config import Settings as S
    from tests.reference import FIXTURES_DIR

    with TestClient(create_app(S(data_dir=FIXTURES_DIR, database_url="sqlite:///:memory:"))) as c:
        assert c.get(f"{API}/demo").json() == {"enabled": False, "notice": None, "questions": []}
