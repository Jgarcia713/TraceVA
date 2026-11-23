"""Filter semantics, validation, and canonical definitions on hand-built frames."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from app.analysis.views import (
    CHILD_NULL_POLICY,
    apply_filters,
    canonical_filter,
    combine_filters,
    definition_sha256,
    rows_excluded_missing,
    validate_filters,
    view_warnings,
)
from app.schemas.tools import Filter, ToolError, ToolErrorCode


@pytest.fixture
def frame() -> pd.DataFrame:
    #   age   sex       wc
    #   25    Male      Private
    #   41    Female    -
    #   -     Male      State-gov
    #   60    -         Private
    #   41    Female    Private
    return pd.DataFrame(
        {
            "age": pd.array([25, 41, None, 60, 41], dtype="Int64"),
            "sex": pd.Categorical(["Male", "Female", "Male", None, "Female"]),
            "wc": pd.Categorical(["Private", None, "State-gov", "Private", "Private"]),
        }
    )


def F(column: str, op: str, value: object = None) -> Filter:  # noqa: N802
    return Filter.model_validate({"column": column, "op": op, "value": value})


def rows(frame: pd.DataFrame, *filters: Filter) -> list[int]:
    return list(apply_filters(frame, filters).index)


# --- semantics ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flt", "expected"),
    [
        (("age", "gt", 40), [1, 3, 4]),
        (("age", "ge", 41), [1, 3, 4]),
        (("age", "lt", 41), [0]),
        (("age", "le", 41), [0, 1, 4]),
        (("age", "eq", 41), [1, 4]),
        (("age", "eq", 41.0), [1, 4]),
        (("sex", "eq", "Female"), [1, 4]),
        (("wc", "in", ["Private", "State-gov"]), [0, 2, 3, 4]),
        (("wc", "is_missing", None), [1]),
        (("sex", "not_missing", None), [0, 1, 2, 4]),
    ],
)
def test_filter_operators(frame: pd.DataFrame, flt: tuple, expected: list[int]) -> None:
    assert rows(frame, F(*flt)) == expected


@pytest.mark.parametrize(
    ("flt", "expected"),
    [
        # pandas would treat missing != x as True; our contract says missing never matches
        (("sex", "ne", "Male"), [1, 4]),
        (("wc", "not_in", ["State-gov"]), [0, 3, 4]),
        (("age", "ne", 25), [1, 3, 4]),
        (("age", "lt", 100), [0, 1, 3, 4]),
    ],
)
def test_missing_never_matches_comparisons(
    frame: pd.DataFrame, flt: tuple, expected: list[int]
) -> None:
    assert rows(frame, F(*flt)) == expected


def test_filters_combine_with_and(frame: pd.DataFrame) -> None:
    assert rows(frame, F("age", "gt", 40), F("sex", "eq", "Female")) == [1, 4]


def test_excluded_missing_counts(frame: pd.DataFrame) -> None:
    assert rows_excluded_missing(frame, F("age", "gt", 40)) == 1
    assert rows_excluded_missing(frame, F("wc", "ne", "Private")) == 1
    assert rows_excluded_missing(frame, F("wc", "is_missing")) == 0


# --- validation -------------------------------------------------------------------


def test_unknown_column(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        validate_filters(frame, [F("salary", "gt", 1)])
    assert info.value.code == ToolErrorCode.UNKNOWN_COLUMN


def test_order_op_on_categorical_rejected(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        validate_filters(frame, [F("sex", "gt", "Female")])
    assert info.value.code == ToolErrorCode.INVALID_FILTER


def test_non_numeric_value_on_numeric_column_rejected(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        validate_filters(frame, [F("age", "gt", "40")])
    assert info.value.code == ToolErrorCode.INVALID_FILTER


def test_unknown_category_suggests_correct_spelling(frame: pd.DataFrame) -> None:
    with pytest.raises(ToolError) as info:
        validate_filters(frame, [F("sex", "eq", "male")])
    assert info.value.code == ToolErrorCode.UNKNOWN_VALUE
    assert info.value.details["suggestions"]["male"][0] == "Male"


@pytest.mark.parametrize(
    "payload",
    [
        {"column": "age", "op": "gt"},  # missing value
        {"column": "age", "op": "gt", "value": [1, 2]},  # list for scalar op
        {"column": "wc", "op": "in", "value": []},  # empty list
        {"column": "wc", "op": "in", "value": "Private"},  # scalar for list op
        {"column": "wc", "op": "is_missing", "value": "x"},  # value for missing op
        {"column": "age", "op": "between", "value": 1},  # unknown op
        {"column": "age", "op": "gt", "value": 1, "sql": "1=1"},  # extra field
        {"column": "age", "op": "gt", "value": True},  # bool is not coerced to 1
        {"column": "wc", "op": "in", "value": ["Private", False]},
    ],
)
def test_filter_shape_validation(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Filter.model_validate(payload)


# --- canonical definitions ------------------------------------------------------------


def test_canonical_form_normalizes_values_and_lists() -> None:
    assert canonical_filter(F("age", "gt", 40.0)) == {"column": "age", "op": "gt", "value": 40}
    assert canonical_filter(F("wc", "in", ["b", "a", "b"]))["value"] == ["a", "b"]


def test_definition_is_order_and_path_independent() -> None:
    a, b = F("age", "gt", 40), F("sex", "eq", "Female")
    one_step = combine_filters([], [a, b])
    reversed_order = combine_filters([], [b, a])
    chained = combine_filters(combine_filters([], [a]), [b])
    assert one_step == reversed_order == chained
    assert combine_filters(one_step, [a]) == one_step  # re-adding a filter is a no-op
    cols = ["age", "sex"]
    digest = definition_sha256("src_1", one_step, cols, CHILD_NULL_POLICY)
    assert digest == definition_sha256("src_1", chained, cols, CHILD_NULL_POLICY)
    assert digest != definition_sha256("src_2", chained, cols, CHILD_NULL_POLICY)


def test_view_warnings() -> None:
    assert "empty" in view_warnings(0)[0]
    assert "small view" in view_warnings(5)[0]
    assert view_warnings(30) == []
