"""Pure, deterministic statistics on dataframes. No database or file access.

Conventions (surfaced in tool contracts):
- `n` is always the non-missing count and the denominator of proportions.
- Missing group keys form an explicit group (key value `None`), never silently dropped.
- `std` is the sample standard deviation (ddof=1); `None` when n < 2.
- Groups are ordered by size descending, then key (missing keys last); truncation
  keeps the largest groups.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from app.analysis.datasets import column_kind
from app.schemas.tools import (
    CATEGORICAL_METRICS,
    NUMERIC_METRICS,
    CategoryProportion,
    ColumnProfile,
    ColumnSummary,
    GroupSummary,
    Metric,
    ScalarValue,
    ToolError,
    ToolErrorCode,
    ValueCount,
)

TOP_VALUES = 5


def to_scalar(value: Any) -> ScalarValue:
    """Convert pandas/numpy scalars to JSON-safe Python values; missing -> None."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()  # type: ignore[no-any-return]
    return value  # type: ignore[no-any-return]


def _float_or_none(value: Any) -> float | None:
    scalar = to_scalar(value)
    return None if scalar is None else float(scalar)


def _sorted_value_counts(values: pd.Series) -> list[tuple[ScalarValue, int]]:
    counts = values.value_counts(dropna=True)
    pairs = [(to_scalar(v), int(c)) for v, c in counts.items() if c > 0]
    pairs.sort(key=lambda item: (-item[1], str(item[0])))
    return pairs


# ---------------------------------------------------------------------------
# Profiling (inspect_dataset)
# ---------------------------------------------------------------------------


def profile_frame(frame: pd.DataFrame) -> list[ColumnProfile]:
    total = len(frame)
    profiles: list[ColumnProfile] = []
    for col in frame.columns:
        series = frame[col]
        non_missing = series.dropna()
        n_missing = int(series.isna().sum())
        kind = column_kind(series)
        profile = ColumnProfile(
            name=str(col),
            kind=kind,  # type: ignore[arg-type]
            dtype=str(series.dtype),
            n_missing=n_missing,
            missing_fraction=(n_missing / total) if total else 0.0,
            n_unique=int(non_missing.nunique()),
        )
        if kind == "numeric":
            profile.min = _float_or_none(non_missing.min()) if len(non_missing) else None
            profile.max = _float_or_none(non_missing.max()) if len(non_missing) else None
        else:
            profile.top_values = [
                ValueCount(value=v, count=c)
                for v, c in _sorted_value_counts(non_missing)[:TOP_VALUES]
            ]
        profiles.append(profile)
    return profiles


# ---------------------------------------------------------------------------
# Summaries (summarize)
# ---------------------------------------------------------------------------


def validate_summarize_request(
    frame: pd.DataFrame,
    columns: Sequence[str],
    group_by: Sequence[str],
    metrics: Sequence[Metric],
) -> None:
    available = [str(c) for c in frame.columns]
    unknown = [c for c in [*columns, *group_by] if c not in available]
    if unknown:
        raise ToolError(
            ToolErrorCode.UNKNOWN_COLUMN,
            f"unknown column(s): {unknown}",
            {"unknown": unknown, "available": available},
        )
    mismatches = []
    for col in columns:
        kind = column_kind(frame[col])
        allowed = NUMERIC_METRICS if kind == "numeric" else CATEGORICAL_METRICS
        mismatches += [
            {"column": col, "kind": kind, "metric": str(m)} for m in metrics if m not in allowed
        ]
    if mismatches:
        raise ToolError(
            ToolErrorCode.METRIC_NOT_APPLICABLE,
            "every metric must apply to every column; numeric metrics need numeric "
            "columns and 'proportions' needs categorical columns",
            {"mismatches": mismatches},
        )


def summarize_column(
    series: pd.Series, metrics: Sequence[Metric], max_categories: int
) -> ColumnSummary:
    non_missing = series.dropna()
    n = len(non_missing)
    summary = ColumnSummary(n=n, n_missing=len(series) - n)
    requested = set(metrics)

    if requested & NUMERIC_METRICS:
        values = non_missing.astype("float64")
        if Metric.MEAN in requested:
            summary.mean = float(values.mean()) if n else None
        if Metric.MEDIAN in requested:
            summary.median = float(values.median()) if n else None
        if Metric.STD in requested:
            summary.std = float(values.std(ddof=1)) if n >= 2 else None
        if Metric.MIN in requested:
            summary.min = float(values.min()) if n else None
        if Metric.MAX in requested:
            summary.max = float(values.max()) if n else None

    if Metric.PROPORTIONS in requested:
        pairs = _sorted_value_counts(non_missing)
        kept, rest = pairs[:max_categories], pairs[max_categories:]
        summary.proportions = [
            CategoryProportion(value=v, count=c, proportion=c / n) for v, c in kept
        ]
        summary.other_count = sum(c for _, c in rest)
    return summary


def _key_sort(key: tuple[ScalarValue, ...]) -> tuple[tuple[int, Any], ...]:
    # Missing keys sort last; non-missing values of one column share a type.
    return tuple((1, 0) if v is None else (0, v) for v in key)


def summarize_frame(
    frame: pd.DataFrame,
    columns: Sequence[str],
    group_by: Sequence[str],
    metrics: Sequence[Metric],
    max_rows: int,
) -> tuple[list[GroupSummary], int, bool, list[str]]:
    """Return (groups, total_groups, truncated, warnings). Raises ToolError on bad input."""
    validate_summarize_request(frame, columns, group_by, metrics)
    warnings: list[str] = []
    if len(frame) == 0:
        warnings.append("view has 0 rows; all statistics are empty")

    def build(key: dict[str, ScalarValue], part: pd.DataFrame) -> GroupSummary:
        return GroupSummary(
            key=key,
            n_rows=len(part),
            columns={c: summarize_column(part[c], metrics, max_rows) for c in columns},
        )

    if not group_by:
        return [build({}, frame)], 1, False, warnings

    # Assign each row a group code (missing keys included), then rank groups by size
    # before materializing any summaries: high-cardinality group_by stays cheap.
    grouped = frame.groupby(list(group_by), dropna=False, observed=True, sort=False)
    codes = grouped.ngroup().to_numpy()
    sizes = np.bincount(codes) if len(codes) else np.array([], dtype=int)
    _, first_rows = np.unique(codes, return_index=True)
    key_frame = frame.iloc[first_rows][list(group_by)]
    keys = [tuple(to_scalar(v) for v in row) for row in key_frame.itertuples(index=False)]

    order = sorted(range(len(keys)), key=lambda g: (-int(sizes[g]), _key_sort(keys[g])))
    total_groups = len(order)
    truncated = total_groups > max_rows
    if truncated:
        warnings.append(
            f"{total_groups} groups exceed the cap of {max_rows}; "
            f"only the {max_rows} largest are returned"
        )

    groups = [
        build(dict(zip(group_by, keys[g], strict=True)), frame[codes == g])
        for g in order[:max_rows]
    ]
    return groups, total_groups, truncated, warnings
