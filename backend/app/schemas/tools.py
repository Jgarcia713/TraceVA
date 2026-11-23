"""Typed contracts for analytical tools.

Inputs are strict (`extra="forbid"`) because they will be exposed to an LLM as JSON
Schema in Milestone 2; unknown or misspelled arguments must fail validation rather than
being silently ignored.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

ColumnName = str
ScalarValue = str | int | float | bool | None


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolErrorCode(StrEnum):
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    UNKNOWN_VIEW = "UNKNOWN_VIEW"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    METRIC_NOT_APPLICABLE = "METRIC_NOT_APPLICABLE"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNSUPPORTED_VIEW = "UNSUPPORTED_VIEW"
    DATASET_INTEGRITY = "DATASET_INTEGRITY"
    # Raised by the agent tool registry / orchestrator rather than by tools themselves
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    INVALID_JSON = "INVALID_JSON"
    TOO_MANY_TOOL_CALLS = "TOO_MANY_TOOL_CALLS"
    INVALID_CITATIONS = "INVALID_CITATIONS"
    INVALID_FILTER = "INVALID_FILTER"
    UNKNOWN_VALUE = "UNKNOWN_VALUE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolError(Exception):
    """Machine-readable tool failure. Never produces evidence (brief §6, invariant 5)."""

    def __init__(
        self, code: ToolErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "details": self.details}


# ---------------------------------------------------------------------------
# inspect_dataset
# ---------------------------------------------------------------------------

ColumnKind = Literal["numeric", "categorical"]


class InspectDatasetInput(StrictInput):
    source_id: str = Field(description="ID of a registered dataset source (src_...).")


class ValueCount(BaseModel):
    value: ScalarValue
    count: int


class ColumnProfile(BaseModel):
    name: ColumnName
    kind: ColumnKind
    dtype: str
    n_missing: int
    missing_fraction: float
    n_unique: int = Field(description="Distinct non-missing values.")
    min: float | None = None
    max: float | None = None
    top_values: list[ValueCount] | None = Field(
        default=None, description="Most frequent categories (categorical columns only)."
    )


class InspectDatasetOutput(BaseModel):
    source_id: str
    name: str
    root_view_id: str
    sha256: str
    parser_version: str
    row_count: int
    columns: list[ColumnProfile]


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


class Metric(StrEnum):
    MEAN = "mean"
    MEDIAN = "median"
    STD = "std"
    MIN = "min"
    MAX = "max"
    PROPORTIONS = "proportions"


NUMERIC_METRICS = frozenset({Metric.MEAN, Metric.MEDIAN, Metric.STD, Metric.MIN, Metric.MAX})
CATEGORICAL_METRICS = frozenset({Metric.PROPORTIONS})


class SummarizeInput(StrictInput):
    view_id: str = Field(description="ID of a dataset view (view_...).")
    columns: list[ColumnName] = Field(
        min_length=1,
        max_length=20,
        description="Columns to compute statistics for. Do not include group_by columns here.",
    )
    group_by: list[ColumnName] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "Optional columns to group rows by (at most 2). Each group is reported "
            "separately; group_by columns must not also appear in columns."
        ),
    )
    metrics: list[Metric] = Field(
        min_length=1,
        description=(
            "Numeric metrics (mean, median, std, min, max) require numeric columns; "
            "'proportions' requires categorical columns. Every metric must apply to "
            "every requested column."
        ),
    )

    @model_validator(mode="after")
    def _check_lists(self) -> SummarizeInput:
        for field in ("columns", "group_by", "metrics"):
            values = getattr(self, field)
            if len(set(values)) != len(values):
                raise ValueError(f"{field} contains duplicates")
        overlap = set(self.columns) & set(self.group_by)
        if overlap:
            raise ValueError(f"columns and group_by overlap: {sorted(overlap)}")
        return self


class CategoryProportion(BaseModel):
    value: ScalarValue
    count: int
    proportion: float = Field(description="count / n (non-missing values in the group).")


class ColumnSummary(BaseModel):
    n: int = Field(description="Non-missing values in this group: the denominator.")
    n_missing: int
    mean: float | None = None
    median: float | None = None
    std: float | None = Field(default=None, description="Sample std (ddof=1); null if n < 2.")
    min: float | None = None
    max: float | None = None
    proportions: list[CategoryProportion] | None = None
    other_count: int | None = Field(
        default=None, description="Count of categories omitted by the result-size cap."
    )


class GroupSummary(BaseModel):
    key: dict[ColumnName, ScalarValue] = Field(
        description="Group-by values; null means the group of rows where that key is missing."
    )
    n_rows: int
    columns: dict[ColumnName, ColumnSummary]


class SummarizeOutput(BaseModel):
    view_id: str
    row_count: int
    group_by: list[ColumnName]
    metrics: list[Metric]
    total_groups: int
    truncated: bool = Field(description="True if groups were cut to the result-size cap.")
    groups: list[GroupSummary] = Field(
        description="Ordered by n_rows descending, then key; truncation keeps the largest."
    )
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# create_view
# ---------------------------------------------------------------------------

# Strict: in lax mode pydantic coerces true -> 1, silently turning a bad filter into a
# different, valid one.
FilterValue = StrictStr | StrictInt | StrictFloat


class FilterOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    NOT_IN = "not_in"
    IS_MISSING = "is_missing"
    NOT_MISSING = "not_missing"


ORDER_OPS = frozenset({FilterOp.LT, FilterOp.LE, FilterOp.GT, FilterOp.GE})
LIST_OPS = frozenset({FilterOp.IN, FilterOp.NOT_IN})
MISSING_OPS = frozenset({FilterOp.IS_MISSING, FilterOp.NOT_MISSING})


class Filter(StrictInput):
    column: ColumnName
    op: FilterOp = Field(
        description=(
            "eq/ne/lt/le/gt/ge compare with value; in/not_in take a list; "
            "is_missing/not_missing take no value. lt/le/gt/ge need a numeric column."
        )
    )
    value: FilterValue | list[FilterValue] | None = Field(
        default=None,
        description="Category values must match exactly (case-sensitive).",
    )

    @model_validator(mode="after")
    def _check_value_shape(self) -> Filter:
        if self.op in MISSING_OPS:
            if self.value is not None:
                raise ValueError(f"{self.op} takes no value")
        elif self.op in LIST_OPS:
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 50:
                raise ValueError(f"{self.op} needs a list of 1-50 values")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"{self.op} needs a single value")
        return self


class CreateViewInput(StrictInput):
    view_id: str = Field(description="View to filter (e.g. the dataset's root view_id).")
    filters: list[Filter] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Conditions combined with AND. Rows with a missing value in a filtered "
            "column never match a comparison (including ne and not_in)."
        ),
    )


class FilterReport(BaseModel):
    column: ColumnName
    op: FilterOp
    value: FilterValue | list[FilterValue] | None = None
    rows_excluded_missing: int = Field(
        description="Rows of the base view excluded because this column was missing."
    )


class CreateViewOutput(BaseModel):
    view_id: str
    based_on_view_id: str
    source_id: str
    reused: bool = Field(description="True if an identical view already existed.")
    base_row_count: int
    row_count: int
    filters: list[dict[str, Any]] = Field(
        description="Complete canonical filter set of the view (base filters + new)."
    )
    applied: list[FilterReport] = Field(description="The filters added in this call.")
    warnings: list[str] = Field(default_factory=list)
