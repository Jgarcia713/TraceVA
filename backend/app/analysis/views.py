"""Dataset views: validated filter AST, canonical definitions, and deterministic application.

Pure functions (no database or file access).

Semantics (exposed in the tool contract):
- Filters combine with AND.
- A row whose filtered column is missing never matches a comparison, including `ne`
  and `not_in`. Only `is_missing` selects missing values. Rows excluded for this
  reason are counted and reported, never dropped silently.
- A view is identified by its *definition*: source, the complete canonical filter set,
  columns, and null policy. The same definition gives the same view, regardless of
  filter order or of how the filters were chained.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Sequence
from typing import Any

import pandas as pd

from app.analysis.datasets import column_kind
from app.schemas.tools import (
    LIST_OPS,
    MISSING_OPS,
    ORDER_OPS,
    Filter,
    FilterOp,
    FilterValue,
    ToolError,
    ToolErrorCode,
)

CHILD_NULL_POLICY = "missing_fails_comparisons"
SMALL_VIEW_ROWS = 30


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _values(f: Filter) -> list[FilterValue]:
    if f.value is None:
        return []
    return list(f.value) if isinstance(f.value, list) else [f.value]


def validate_filters(frame: pd.DataFrame, filters: Sequence[Filter]) -> None:
    """Raise ToolError for unknown columns, type mismatches, or unknown categories."""
    available = [str(c) for c in frame.columns]
    unknown = sorted({f.column for f in filters} - set(available))
    if unknown:
        raise ToolError(
            ToolErrorCode.UNKNOWN_COLUMN,
            f"unknown column(s): {unknown}",
            {"unknown": unknown, "available": available},
        )
    for f in filters:
        if f.op in MISSING_OPS:
            continue
        series = frame[f.column]
        kind = column_kind(series)
        values = _values(f)
        if kind == "numeric":
            bad = [v for v in values if not _is_number(v)]
            if bad:
                raise ToolError(
                    ToolErrorCode.INVALID_FILTER,
                    f"column {f.column!r} is numeric; values must be numbers",
                    {"column": f.column, "invalid_values": bad},
                )
            continue
        # categorical
        if f.op in ORDER_OPS:
            raise ToolError(
                ToolErrorCode.INVALID_FILTER,
                f"{f.op} needs a numeric column; {f.column!r} is categorical",
                {
                    "column": f.column,
                    "allowed_ops": ["eq", "ne", "in", "not_in", "is_missing", "not_missing"],
                },
            )
        categories = [str(c) for c in series.dropna().unique()]
        missing_values = [v for v in values if str(v) not in categories or not isinstance(v, str)]
        if missing_values:
            suggestions: dict[str, list[str]] = {}
            lowered = {c.lower(): c for c in categories}
            for v in missing_values:
                text = str(v)
                exact_ci = lowered.get(text.lower())
                close = difflib.get_close_matches(text, categories, n=3, cutoff=0.6)
                suggestions[text] = ([exact_ci] if exact_ci else []) + [
                    c for c in close if c != exact_ci
                ]
            raise ToolError(
                ToolErrorCode.UNKNOWN_VALUE,
                f"value(s) not present in column {f.column!r}: {[str(v) for v in missing_values]}",
                {
                    "column": f.column,
                    "suggestions": suggestions,
                    "categories": sorted(categories)[:50],
                },
            )


# ---------------------------------------------------------------------------
# Canonical definitions
# ---------------------------------------------------------------------------


def _normalize_value(value: FilterValue) -> FilterValue:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_filter(f: Filter | dict[str, Any]) -> dict[str, Any]:
    if isinstance(f, dict):
        f = Filter.model_validate(f)
    value: Any
    if f.op in MISSING_OPS:
        value = None
    elif f.op in LIST_OPS:
        normalized = {_normalize_value(v) for v in _values(f)}
        value = sorted(normalized, key=lambda v: (type(v).__name__, v))
    else:
        assert f.value is not None and not isinstance(f.value, list)
        value = _normalize_value(f.value)
    return {"column": f.column, "op": str(f.op), "value": value}


def _filter_key(f: dict[str, Any]) -> str:
    return json.dumps(f, sort_keys=True, separators=(",", ":"))


def combine_filters(base: Sequence[dict[str, Any]], new: Sequence[Filter]) -> list[dict[str, Any]]:
    """Complete canonical filter set: base + new, deduplicated, order-independent."""
    merged = {_filter_key(canonical_filter(f)): canonical_filter(f) for f in base}
    for f in new:
        c = canonical_filter(f)
        merged[_filter_key(c)] = c
    return [merged[k] for k in sorted(merged)]


def definition_sha256(
    source_id: str, filters: Sequence[dict[str, Any]], columns: Sequence[str], null_policy: str
) -> str:
    definition = {
        "source_id": source_id,
        "filters": list(filters),
        "columns": list(columns),
        "null_policy": null_policy,
    }
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def filter_mask(frame: pd.DataFrame, f: Filter | dict[str, Any]) -> pd.Series:
    c = canonical_filter(f)
    series = frame[c["column"]]
    missing = series.isna()
    op = FilterOp(c["op"])
    if op == FilterOp.IS_MISSING:
        return missing
    if op == FilterOp.NOT_MISSING:
        return ~missing
    value = c["value"]
    if op in LIST_OPS:
        matched = series.isin(value)
        if op == FilterOp.NOT_IN:
            matched = ~matched
    elif op == FilterOp.EQ:
        matched = series == value
    elif op == FilterOp.NE:
        matched = series != value
    elif op == FilterOp.LT:
        matched = series < value
    elif op == FilterOp.LE:
        matched = series <= value
    elif op == FilterOp.GT:
        matched = series > value
    else:  # GE
        matched = series >= value
    # Missing never matches a comparison (pandas would treat NaN != x as True).
    return matched.fillna(False).astype(bool) & ~missing


def apply_filters(frame: pd.DataFrame, filters: Sequence[Filter | dict[str, Any]]) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    for f in filters:
        mask &= filter_mask(frame, f)
    return frame[mask]


def rows_excluded_missing(frame: pd.DataFrame, f: Filter) -> int:
    """Rows of `frame` excluded by `f` only because the filtered column is missing."""
    if f.op == FilterOp.IS_MISSING:
        return 0
    return int(frame[f.column].isna().sum())


def view_warnings(row_count: int) -> list[str]:
    if row_count == 0:
        return ["the view is empty: no rows match all filters"]
    if row_count < SMALL_VIEW_ROWS:
        return [f"small view ({row_count} rows): statistics may be unstable"]
    return []
