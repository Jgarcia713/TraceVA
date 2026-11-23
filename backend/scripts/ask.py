"""Ask one analytical question through the agent (Milestone 2 proof of concept).

Usage (from backend/, with Ollama running and 'adult' registered):
    python scripts/ask.py "How do average weekly hours differ between men and women?"
    python scripts/ask.py --conversation conv_... "And for people older than 40?"
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.agent.model_client import OpenAICompatClient
from app.agent.orchestrator import AgentOrchestrator
from app.config import get_settings
from app.db.models import AnalysisRun, ChatMessage, Claim, ClaimEvidence, Evidence, Operation
from app.db.session import create_app_engine, create_session_factory
from app.provenance.graph import claim_assumptions
from app.provenance.text_checks import explanation_part, unverified_numbers


def short(value: object, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the TraceVA agent one question")
    parser.add_argument("question")
    parser.add_argument("--conversation", default=None, help="continue an existing conversation")
    parser.add_argument("--show-evidence", action="store_true", help="print full cited evidence")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_app_engine(settings.database_url)
    sessions = create_session_factory(engine)
    llm = OpenAICompatClient(settings)
    print(f"model={settings.llm_model} thinking={settings.llm_thinking} ...", flush=True)

    result = AgentOrchestrator(sessions, llm, settings).ask(args.question, args.conversation)

    with sessions() as session:
        run = session.get(AnalysisRun, result.run_id)
        assert run is not None
        print(f"\nrun {run.id} [{run.status}] conversation {run.conversation_id}")
        print(f"usage: {run.usage_json}")
        if run.error_json:
            print(f"error: {run.error_json}")
        print("\noperations:")
        ops = session.scalars(
            select(Operation).where(Operation.run_id == run.id).order_by(Operation.sequence_no)
        )
        for op in ops:
            ev = session.scalar(select(Evidence.id).where(Evidence.operation_id == op.id))
            outcome = f"-> {ev}" if ev else f"FAILED {short(op.error_json)}"
            if op.output_ids_json:
                outcome += f" (produced {', '.join(op.output_ids_json)})"
            print(f"  {op.sequence_no}. {op.tool_name}({short(op.raw_arguments, 120)}) {outcome}")
        answer = session.scalar(
            select(ChatMessage).where(ChatMessage.run_id == run.id, ChatMessage.role == "assistant")
        )
        claims = session.scalars(
            select(Claim).where(Claim.run_id == run.id).order_by(Claim.sequence_no)
        ).all()
        if answer:
            print(f"\nanswer (explanation NOT verified):\n{answer.content}")
            loose = unverified_numbers(explanation_part(answer.content), [c.text for c in claims])
            if loose:
                print(f"  numbers in the explanation that appear in no claim: {loose}")
        for claim in claims:
            cited = session.scalars(
                select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == claim.id)
            ).all()
            print(f"\nclaim {claim.id}: {claim.text}\n  cites: {', '.join(cited)}")
            bearing = claim_assumptions(session, claim.id)
            for a in bearing["stated"]:
                print(f"  stated ({a['scope']}-level, {a['status']}): {a['text']}")
            for a in bearing["derived"]:
                n = len(a["operations"])
                print(f"  derived (method template, {a['status']}, {n} op(s)): {a['text']}")
            if args.show_evidence:
                for eid in cited:
                    evidence = session.get(Evidence, eid)
                    assert evidence is not None
                    print(f"  {eid}: {short(evidence.result_json, 2000)}")
    engine.dispose()
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
