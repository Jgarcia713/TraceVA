"""Registry + tools against a migrated database, checked against stdlib references."""

from __future__ import annotations

import dataclasses
import shutil
import statistics
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analysis.datasets import ADULT_COLUMNS, DatasetIntegrityError
from app.analysis.registry import (
    DATASETS,
    UnknownDatasetError,
    register_dataset,
    root_view_id,
)
from app.analysis.tools import ToolContext, inspect_dataset, summarize
from app.config import REPO_DIR
from app.db.models import DatasetSource, DatasetView
from app.schemas.tools import (
    InspectDatasetInput,
    Metric,
    SummarizeInput,
    ToolError,
    ToolErrorCode,
)
from tests.reference import FIXTURES_DIR, raw_rows

SAMPLE = DATASETS["adult_sample"]
COL = {name: i for i, name in enumerate(ADULT_COLUMNS)}


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model))  # type: ignore[return-value]


@pytest.fixture
def ctx(session: Session) -> ToolContext:
    return ToolContext(session=session, data_dir=FIXTURES_DIR, max_result_rows=30)


@pytest.fixture
def sample_source(session: Session) -> DatasetSource:
    source = register_dataset(session, "adult_sample", FIXTURES_DIR)
    session.commit()
    return source


# --- registration -----------------------------------------------------------------


def test_registration_creates_source_and_root_view(
    session: Session, sample_source: DatasetSource
) -> None:
    assert sample_source.id.startswith("src_")
    assert (sample_source.row_count, sample_source.sha256) == (50, SAMPLE.sha256)
    assert [c["name"] for c in sample_source.schema_json] == ADULT_COLUMNS
    assert sample_source.created_at.tzinfo is not None

    view = session.get(DatasetView, root_view_id(session, sample_source.id))
    assert view is not None and view.id.startswith("view_")
    assert view.parent_view_id is None
    assert view.canonical_filters_json == []
    assert view.columns_json == ADULT_COLUMNS
    assert (view.null_policy, view.row_count) == ("keep", 50)


def test_registration_is_idempotent(session: Session, sample_source: DatasetSource) -> None:
    again = register_dataset(session, "adult_sample", FIXTURES_DIR)
    session.commit()
    assert again.id == sample_source.id
    assert (count(session, DatasetSource), count(session, DatasetView)) == (1, 1)


def test_hash_mismatch_is_rejected_and_writes_nothing(session: Session) -> None:
    bad = dataclasses.replace(SAMPLE, sha256="0" * 64)
    with pytest.raises(DatasetIntegrityError, match="hash mismatch"):
        register_dataset(session, bad.name, FIXTURES_DIR, spec=bad)
    session.rollback()
    assert count(session, DatasetSource) == 0


def test_path_escape_is_rejected(session: Session) -> None:
    evil = dataclasses.replace(SAMPLE, relative_path="../conftest.py")
    with pytest.raises(DatasetIntegrityError, match="escapes"):
        register_dataset(session, evil.name, FIXTURES_DIR, spec=evil)


def test_missing_file_is_rejected(session: Session, tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="not found"):
        register_dataset(session, "adult_sample", tmp_path)


def test_unknown_dataset_name(session: Session) -> None:
    with pytest.raises(UnknownDatasetError):
        register_dataset(session, "../../etc/passwd", FIXTURES_DIR)


def test_database_enforces_one_root_view_per_source(
    session: Session, sample_source: DatasetSource
) -> None:
    session.add(
        DatasetView(
            source_id=sample_source.id,
            parent_view_id=None,
            canonical_filters_json=[],
            columns_json=["age"],
            null_policy="keep",
            row_count=50,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_database_enforces_foreign_keys(session: Session) -> None:
    session.add(
        DatasetView(
            source_id="src_does_not_exist",
            canonical_filters_json=[],
            columns_json=[],
            null_policy="keep",
            row_count=0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


# --- inspect_dataset ---------------------------------------------------------------


def test_inspect_matches_reference(ctx: ToolContext, sample_source: DatasetSource) -> None:
    out = inspect_dataset(ctx, InspectDatasetInput(source_id=sample_source.id))
    rows = raw_rows()
    assert out.row_count == len(rows)
    assert out.root_view_id == root_view_id(ctx.session, sample_source.id)
    for profile in out.columns:
        values = [r[COL[profile.name]] for r in rows]
        present = [v for v in values if v != "?"]
        assert profile.n_missing == len(values) - len(present), profile.name
        assert profile.n_unique == len(set(present)), profile.name
        if profile.kind == "numeric":
            assert profile.min == min(map(int, present))
            assert profile.max == max(map(int, present))
        else:
            top = Counter(present).most_common()
            assert profile.top_values is not None
            assert profile.top_values[0].count == top[0][1], profile.name


def test_inspect_unknown_source(ctx: ToolContext) -> None:
    with pytest.raises(ToolError) as info:
        inspect_dataset(ctx, InspectDatasetInput(source_id="src_nope"))
    assert info.value.code == ToolErrorCode.UNKNOWN_SOURCE


# --- summarize -------------------------------------------------------------------


def test_mean_age_by_sex_matches_reference(ctx: ToolContext, sample_source: DatasetSource) -> None:
    view_id = root_view_id(ctx.session, sample_source.id)
    out = summarize(
        ctx,
        SummarizeInput(
            view_id=view_id,
            columns=["age", "hours_per_week"],
            group_by=["sex"],
            metrics=[Metric.MEAN, Metric.MEDIAN, Metric.STD],
        ),
    )
    rows = raw_rows()
    assert out.row_count == 50 and not out.truncated
    for group in out.groups:
        members = [r for r in rows if r[COL["sex"]] == group.key["sex"]]
        assert group.n_rows == len(members)
        for col in ("age", "hours_per_week"):
            ref = [int(r[COL[col]]) for r in members]
            summary = group.columns[col]
            assert summary.n == len(ref)
            assert summary.mean == pytest.approx(statistics.mean(ref))
            assert summary.median == pytest.approx(statistics.median(ref))
            assert summary.std == pytest.approx(statistics.stdev(ref))


def test_workclass_proportions_by_income_match_reference(
    ctx: ToolContext, sample_source: DatasetSource
) -> None:
    view_id = root_view_id(ctx.session, sample_source.id)
    out = summarize(
        ctx,
        SummarizeInput(
            view_id=view_id,
            columns=["workclass"],
            group_by=["income"],
            metrics=[Metric.PROPORTIONS],
        ),
    )
    rows = raw_rows()
    for group in out.groups:
        values = [r[COL["workclass"]] for r in rows if r[COL["income"]] == group.key["income"]]
        present = [v for v in values if v != "?"]
        summary = group.columns["workclass"]
        assert (summary.n, summary.n_missing) == (len(present), len(values) - len(present))
        ref = Counter(present)
        assert summary.proportions is not None
        assert {p.value: p.count for p in summary.proportions} == dict(ref)
        for p in summary.proportions:
            assert p.proportion == pytest.approx(ref[p.value] / len(present))


def test_missing_group_key_counted_explicitly(
    ctx: ToolContext, sample_source: DatasetSource
) -> None:
    view_id = root_view_id(ctx.session, sample_source.id)
    out = summarize(
        ctx,
        SummarizeInput(view_id=view_id, columns=["age"], group_by=["workclass"], metrics=["mean"]),
    )
    missing_group = next(g for g in out.groups if g.key["workclass"] is None)
    expected = sum(1 for r in raw_rows() if r[COL["workclass"]] == "?")
    assert missing_group.n_rows == expected > 0
    assert sum(g.n_rows for g in out.groups) == 50


def test_summarize_unknown_view(ctx: ToolContext) -> None:
    with pytest.raises(ToolError) as info:
        summarize(ctx, SummarizeInput(view_id="view_nope", columns=["age"], metrics=["mean"]))
    assert info.value.code == ToolErrorCode.UNKNOWN_VIEW


def test_tampered_file_detected_after_registration(session: Session, tmp_path: Path) -> None:
    shutil.copy(FIXTURES_DIR / SAMPLE.relative_path, tmp_path / SAMPLE.relative_path)
    source = register_dataset(session, "adult_sample", tmp_path)
    session.commit()

    from app.analysis.datasets import clear_cache

    clear_cache()  # simulate a new process
    with (tmp_path / SAMPLE.relative_path).open("ab") as handle:
        handle.write(b"\n")

    ctx = ToolContext(session=session, data_dir=tmp_path, max_result_rows=30)
    with pytest.raises(ToolError) as info:
        inspect_dataset(ctx, InspectDatasetInput(source_id=source.id))
    assert info.value.code == ToolErrorCode.DATASET_INTEGRITY


# --- full dataset (skipped unless the raw file is present locally) -------------------

FULL_DATA_DIR = REPO_DIR / "datasets"


@pytest.mark.skipif(
    not (FULL_DATA_DIR / DATASETS["adult"].relative_path).is_file(),
    reason="raw adult.data not present (see datasets/README.md)",
)
def test_full_adult_registration(session: Session) -> None:
    source = register_dataset(session, "adult", FULL_DATA_DIR)
    assert source.row_count == 32_561
    ctx = ToolContext(session=session, data_dir=FULL_DATA_DIR, max_result_rows=30)
    out = inspect_dataset(ctx, InspectDatasetInput(source_id=source.id))
    missing = {c.name: c.n_missing for c in out.columns if c.n_missing}
    assert set(missing) == {"workclass", "occupation", "native_country"}
