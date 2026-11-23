"""create_view against the database, checked against a stdlib reading of the fixture."""

from __future__ import annotations

import statistics
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.datasets import ADULT_COLUMNS
from app.analysis.tools import (
    ToolContext,
    ToolResult,
    create_view,
    load_view_frame,
    persist_views,
    summarize,
)
from app.db.models import DatasetView
from app.schemas.tools import CreateViewInput, CreateViewOutput, SummarizeInput, ToolError
from tests.reference import FIXTURES_DIR, raw_rows

COL = {name: i for i, name in enumerate(ADULT_COLUMNS)}


def make_view(db: Session, view_id: str, *filters: dict[str, Any]) -> CreateViewOutput:
    """Call the tool and persist its drafts, as the orchestrator would."""
    ctx = ToolContext(db, FIXTURES_DIR, 30)
    result = create_view(ctx, CreateViewInput(view_id=view_id, filters=list(filters)))
    assert isinstance(result, ToolResult) and isinstance(result.output, CreateViewOutput)
    persist_views(db, result.new_views)
    assert result.output_ids == [result.output.view_id]
    return result.output


def ref_rows(pred: Any) -> list[list[str]]:
    return [r for r in raw_rows() if pred(r)]


def age_over_40(r: list[str]) -> bool:
    return int(r[COL["age"]]) > 40


def test_filtered_view_matches_reference(
    session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    with session_factory.begin() as db:
        out = make_view(db, sample_ids["view_id"], {"column": "age", "op": "gt", "value": 40})
        expected = ref_rows(age_over_40)
        assert (out.base_row_count, out.row_count, out.reused) == (50, len(expected), False)
        assert out.applied[0].rows_excluded_missing == 0

        stored = db.get(DatasetView, out.view_id)
        assert stored is not None and stored.parent_view_id == sample_ids["view_id"]
        assert stored.row_count == len(expected) and stored.definition_sha256

        ctx = ToolContext(db, FIXTURES_DIR, 30)
        summary = summarize(
            ctx,
            SummarizeInput(view_id=out.view_id, columns=["hours_per_week"], metrics=["mean"]),
        )
        mean = summary.groups[0].columns["hours_per_week"].mean
        assert mean == pytest.approx(
            statistics.mean(int(r[COL["hours_per_week"]]) for r in expected)
        )


def test_missing_values_are_excluded_and_reported(
    session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    with session_factory.begin() as db:
        out = make_view(
            db, sample_ids["view_id"], {"column": "workclass", "op": "ne", "value": "Private"}
        )
    missing = len(ref_rows(lambda r: r[COL["workclass"]] == "?"))
    expected = ref_rows(lambda r: r[COL["workclass"]] not in ("Private", "?"))
    assert missing > 0
    assert out.applied[0].rows_excluded_missing == missing
    assert out.row_count == len(expected)


def test_identical_definitions_reuse_one_view(
    session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    root = sample_ids["view_id"]
    older = {"column": "age", "op": "gt", "value": 40}
    women = {"column": "sex", "op": "eq", "value": "Female"}
    with session_factory.begin() as db:
        both = make_view(db, root, older, women)
        reordered = make_view(db, root, women, {"column": "age", "op": "gt", "value": 40.0})
        first = make_view(db, root, older)
        chained = make_view(db, first.view_id, women)
        count = db.scalar(select(func.count()).select_from(DatasetView))

    assert not both.reused and reordered.reused and chained.reused
    assert both.view_id == reordered.view_id == chained.view_id
    assert count == 3  # root, age>40, age>40 AND Female
    expected = ref_rows(lambda r: age_over_40(r) and r[COL["sex"]] == "Female")
    assert both.row_count == len(expected)


def test_view_replay_is_deterministic(
    session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    from app.analysis.datasets import clear_cache

    with session_factory.begin() as db:
        out = make_view(
            db, sample_ids["view_id"], {"column": "education_num", "op": "ge", "value": 13}
        )
        ctx = ToolContext(db, FIXTURES_DIR, 30)
        first = load_view_frame(ctx, out.view_id)
        clear_cache()  # a fresh process reparses and re-verifies the source
        second = load_view_frame(ctx, out.view_id)
    assert first.equals(second) and len(first) == out.row_count


def test_empty_view_is_valid_with_warning(
    session_factory: sessionmaker[Session], sample_ids: dict[str, str]
) -> None:
    with session_factory.begin() as db:
        out = make_view(
            db,
            sample_ids["view_id"],
            {"column": "age", "op": "gt", "value": 60},
            {"column": "age", "op": "lt", "value": 30},
        )
        ctx = ToolContext(db, FIXTURES_DIR, 30)
        summary = summarize(
            ctx, SummarizeInput(view_id=out.view_id, columns=["age"], metrics=["mean"])
        )
    assert out.row_count == 0 and "empty" in out.warnings[0]
    assert summary.row_count == 0 and summary.groups[0].columns["age"].mean is None


def test_errors(session_factory: sessionmaker[Session], sample_ids: dict[str, str]) -> None:
    with session_factory.begin() as db:
        ctx = ToolContext(db, FIXTURES_DIR, 30)
        with pytest.raises(ToolError) as unknown_view:
            create_view(
                ctx,
                CreateViewInput(
                    view_id="view_nope", filters=[{"column": "age", "op": "gt", "value": 1}]
                ),
            )
        with pytest.raises(ToolError) as question_mark:
            create_view(
                ctx,
                CreateViewInput(
                    view_id=sample_ids["view_id"],
                    filters=[{"column": "workclass", "op": "eq", "value": "?"}],
                ),
            )
    assert unknown_view.value.code == "UNKNOWN_VIEW"
    # The error names valid starting points instead of leaving the model to guess.
    (known,) = unknown_view.value.details["registered_datasets"]
    assert known["root_view_id"] == sample_ids["view_id"]
    # '?' is a missing marker in the raw file, not a category; is_missing is the way.
    assert question_mark.value.code == "UNKNOWN_VALUE"
