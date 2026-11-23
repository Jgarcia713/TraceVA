"""summarize/profile semantics on hand-built frames with hand-computed expectations."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from pydantic import ValidationError

from app.analysis.statistics import profile_frame, summarize_frame
from app.schemas.tools import Metric, SummarizeInput, ToolError, ToolErrorCode

M = Metric


@pytest.fixture
def frame() -> pd.DataFrame:
    #   x      g     c     k
    #   1      a     u     5
    #   2      a     v     5
    #   3      b     u     5
    #   4      b     -     5
    #   -      -     u     5
    return pd.DataFrame(
        {
            "x": pd.array([1, 2, 3, 4, None], dtype="Int64"),
            "g": pd.Categorical(["a", "a", "b", "b", None]),
            "c": pd.Categorical(["u", "v", "u", None, "u"], categories=["u", "v", "unused"]),
            "k": pd.array([5, 5, 5, 5, 5], dtype="Int64"),
        }
    )


def run(frame: pd.DataFrame, columns, group_by=(), metrics=(M.MEAN,), max_rows=30):
    return summarize_frame(frame, list(columns), list(group_by), list(metrics), max_rows)


# --- ungrouped numeric -----------------------------------------------------------


def test_numeric_metrics_ungrouped(frame: pd.DataFrame) -> None:
    groups, total, truncated, warnings = run(
        frame, ["x"], metrics=[M.MEAN, M.MEDIAN, M.STD, M.MIN, M.MAX]
    )
    assert (total, truncated, warnings) == (1, False, [])
    (group,) = groups
    assert group.key == {} and group.n_rows == 5
    s = group.columns["x"]
    assert (s.n, s.n_missing) == (4, 1)
    assert s.mean == 2.5 and s.median == 2.5
    assert s.std == pytest.approx(math.sqrt(5 / 3))  # sample std of 1..4
    assert (s.min, s.max) == (1.0, 4.0)


def test_only_requested_metrics_are_populated(frame: pd.DataFrame) -> None:
    s = run(frame, ["x"], metrics=[M.MEAN])[0][0].columns["x"]
    assert s.mean == 2.5
    assert s.median is None and s.std is None and s.proportions is None


def test_constant_column_has_zero_std(frame: pd.DataFrame) -> None:
    assert run(frame, ["k"], metrics=[M.STD])[0][0].columns["k"].std == 0.0


# --- grouping ---------------------------------------------------------------------


def test_group_by_keeps_missing_key_group(frame: pd.DataFrame) -> None:
    groups, total, _, _ = run(frame, ["x"], group_by=["g"], metrics=[M.MEAN, M.STD])
    assert total == 3
    assert [g.key for g in groups] == [{"g": "a"}, {"g": "b"}, {"g": None}]
    assert [g.n_rows for g in groups] == [2, 2, 1]
    a, b, missing = (g.columns["x"] for g in groups)
    assert (a.mean, b.mean) == (1.5, 3.5)
    assert a.std == pytest.approx(math.sqrt(0.5))
    # The missing-key group's only x is also missing: explicit zero denominator.
    assert (missing.n, missing.n_missing, missing.mean, missing.std) == (0, 1, None, None)


def test_group_sizes_sum_to_row_count(frame: pd.DataFrame) -> None:
    groups, *_ = run(frame, ["x"], group_by=["g", "c"])
    assert sum(g.n_rows for g in groups) == len(frame)


def test_two_level_group_by_with_missing(frame: pd.DataFrame) -> None:
    groups, total, _, _ = run(frame, ["x"], group_by=["g", "c"])
    keys = {(g.key["g"], g.key["c"]): g.n_rows for g in groups}
    assert keys == {("a", "u"): 1, ("a", "v"): 1, ("b", "u"): 1, ("b", None): 1, (None, "u"): 1}
    assert total == 5


def test_truncation_keeps_largest_groups() -> None:
    frame = pd.DataFrame(
        {
            "x": pd.array(range(10), dtype="Int64"),
            "g": pd.Categorical(list("aaaabbbccd")),
        }
    )
    groups, total, truncated, warnings = run(frame, ["x"], group_by=["g"], max_rows=2)
    assert (total, truncated) == (4, True)
    assert [g.key["g"] for g in groups] == ["a", "b"]
    assert "exceed the cap" in warnings[0]


def test_equal_sized_groups_are_ordered_by_key() -> None:
    frame = pd.DataFrame(
        {"x": pd.array([1, 2, 3], dtype="Int64"), "g": pd.Categorical(["c", "a", "b"])}
    )
    groups, *_ = run(frame, ["x"], group_by=["g"])
    assert [g.key["g"] for g in groups] == ["a", "b", "c"]


# --- proportions ------------------------------------------------------------------


def test_proportions_use_non_missing_denominator(frame: pd.DataFrame) -> None:
    s = run(frame, ["c"], metrics=[M.PROPORTIONS])[0][0].columns["c"]
    assert (s.n, s.n_missing, s.other_count) == (4, 1, 0)
    assert [(p.value, p.count, p.proportion) for p in s.proportions] == [
        ("u", 3, 0.75),
        ("v", 1, 0.25),
    ]  # unused category not reported
    assert sum(p.proportion for p in s.proportions) == pytest.approx(1.0)


def test_grouped_proportions(frame: pd.DataFrame) -> None:
    groups, *_ = run(frame, ["c"], group_by=["g"], metrics=[M.PROPORTIONS])
    by_key = {g.key["g"]: g.columns["c"] for g in groups}
    assert [(p.value, p.proportion) for p in by_key["a"].proportions] == [("u", 0.5), ("v", 0.5)]
    assert (by_key["b"].n, by_key["b"].n_missing) == (1, 1)
    assert [(p.value, p.proportion) for p in by_key["b"].proportions] == [("u", 1.0)]


def test_proportions_category_cap_reports_other_count() -> None:
    frame = pd.DataFrame({"c": pd.Categorical(list("aaabbc"))})
    s = run(frame, ["c"], metrics=[M.PROPORTIONS], max_rows=2)[0][0].columns["c"]
    assert [p.value for p in s.proportions] == ["a", "b"]
    assert s.other_count == 1


# --- edge cases and errors ----------------------------------------------------------


def test_empty_frame_returns_explicit_empty_result(frame: pd.DataFrame) -> None:
    empty = frame.iloc[0:0]
    groups, total, _, warnings = run(empty, ["x"], metrics=[M.MEAN, M.STD])
    s = groups[0].columns["x"]
    assert (total, s.n, s.mean, s.std) == (1, 0, None, None)
    assert "0 rows" in warnings[0]
    grouped, total, _, _ = run(empty, ["x"], group_by=["g"])
    assert (grouped, total) == ([], 0)


def test_rejects_numeric_metric_on_categorical(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        run(frame, ["x", "c"], metrics=[M.MEAN])
    assert info.value.code == ToolErrorCode.METRIC_NOT_APPLICABLE
    assert info.value.details["mismatches"] == [
        {"column": "c", "kind": "categorical", "metric": "mean"}
    ]


def test_rejects_proportions_on_numeric(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        run(frame, ["x"], metrics=[M.PROPORTIONS])
    assert info.value.code == ToolErrorCode.METRIC_NOT_APPLICABLE


@pytest.mark.parametrize(("columns", "group_by"), [(["nope"], []), (["x"], ["nope"])])
def test_rejects_unknown_columns(frame: pd.DataFrame, columns, group_by) -> None:
    with pytest.raises(ToolError) as info:
        run(frame, columns, group_by=group_by)
    assert info.value.code == ToolErrorCode.UNKNOWN_COLUMN
    assert info.value.details["unknown"] == ["nope"]


def test_output_is_json_serializable(frame: pd.DataFrame) -> None:
    groups, *_ = run(frame, ["c"], group_by=["g"], metrics=[M.PROPORTIONS])
    for g in groups:
        g.model_dump_json()  # fails on numpy scalars / NaN leaking through


# --- input contract ---------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"view_id": "v", "columns": ["x", "x"], "metrics": ["mean"]},
        {"view_id": "v", "columns": ["x"], "group_by": ["x"], "metrics": ["mean"]},
        {"view_id": "v", "columns": ["x"], "group_by": ["a", "b", "c"], "metrics": ["mean"]},
        {"view_id": "v", "columns": ["x"], "metrics": []},
        {"view_id": "v", "columns": [], "metrics": ["mean"]},
        {"view_id": "v", "columns": ["x"], "metrics": ["variance"]},
        {"view_id": "v", "columns": ["x"], "metrics": ["mean"], "sql": "DROP TABLE"},
    ],
)
def test_summarize_input_rejects_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        SummarizeInput.model_validate(payload)


# --- profiling --------------------------------------------------------------------


def test_profile_frame(frame: pd.DataFrame) -> None:
    profiles = {p.name: p for p in profile_frame(frame)}
    x, c = profiles["x"], profiles["c"]
    assert (x.kind, x.n_missing, x.missing_fraction, x.n_unique) == ("numeric", 1, 0.2, 4)
    assert (x.min, x.max, x.top_values) == (1.0, 4.0, None)
    assert (c.kind, c.n_missing, c.n_unique) == ("categorical", 1, 2)
    assert [(v.value, v.count) for v in c.top_values] == [("u", 3), ("v", 1)]
