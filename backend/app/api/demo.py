"""Demo mode metadata: which recorded questions can be replayed."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["demo"])

NOTICE = (
    "Demo mode: model responses are replayed from recorded runs of a real local model "
    "(qwen3.5:4b). Tool execution, evidence, citation checks, assumptions, and the "
    "provenance graph are computed live on the real data."
)


class DemoQuestion(BaseModel):
    question: str
    original_model: str
    prompt_version: str
    recorded_wall_s: float | None = None
    recorded_model_calls: int | None = None
    recorded_failed_operations: int | None = None


class DemoInfo(BaseModel):
    enabled: bool
    notice: str | None
    questions: list[DemoQuestion]


@router.get("/demo", response_model=DemoInfo)
def demo_info(request: Request) -> DemoInfo:
    catalog = request.app.state.demo_catalog
    return DemoInfo(
        enabled=bool(catalog),
        notice=NOTICE if catalog else None,
        questions=[
            DemoQuestion(
                question=r.question,
                original_model=r.model,
                prompt_version=r.prompt_version,
                recorded_wall_s=r.recorded.get("wall_s"),
                recorded_model_calls=r.recorded.get("model_calls"),
                recorded_failed_operations=r.recorded.get("failed_operations"),
            )
            for r in catalog.values()
        ],
    )
