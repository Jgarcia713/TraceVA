"""Contracts between the orchestrator and the model that are not analytical tools."""

from __future__ import annotations

from pydantic import Field

from app.schemas.tools import StrictInput


class ClaimDraft(StrictInput):
    text: str = Field(
        min_length=1,
        max_length=1000,
        description="One concise, checkable statement. Numbers must come from cited evidence.",
    )
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=10,
        description="evidence_id values (ev_...) returned by tool calls in this run.",
    )
    caveats: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Optional limitations that apply to this claim specifically.",
    )


class SubmitAnswerInput(StrictInput):
    """Terminal call: ends the run. Not an analytical operation."""

    explanation: str = Field(
        min_length=1, max_length=4000, description="Short answer to the user's question."
    )
    claims: list[ClaimDraft] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Grounded claims. Leave empty only if the question cannot be answered "
            "with the available tools, and say why in the explanation."
        ),
    )
    caveats: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Limitations of the answer as a whole: missing data, association is not "
            "causation, scope, etc. Put claim-specific limitations on the claim instead."
        ),
    )
