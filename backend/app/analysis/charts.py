"""Deterministic evidence -> chart mapping (backend-owned chart specs, brief §7).

A small validated intermediate schema that the frontend maps to Plotly. The model does
not choose charts yet (a later `build_chart_spec` tool may); here every chart is a pure
function of stored evidence, so it is reproducible and carries the evidence ID.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChartValue = str | int | float | None


class ChartSeries(BaseModel):
    name: str
    x: list[ChartValue]
    y: list[float | None]
    n: list[int] = Field(description="Denominator behind each value (non-missing count).")


class ChartSpec(BaseModel):
    chart_type: Literal["bar"]
    title: str
    x_label: str
    y_label: str
    series: list[ChartSeries]
    barmode: Literal["group", "stack"] = "group"
    y_format: Literal["number", "percent"] = "number"
    evidence_id: str
    notes: list[str] = Field(default_factory=list)


NUMERIC_ORDER = ("mean", "median", "min", "max", "std")


def _label(value: ChartValue) -> ChartValue:
    return "(missing)" if value is None else value


def _summarize_chart(evidence_id: str, result: dict[str, Any]) -> ChartSpec | None:
    groups = result.get("groups") or []
    group_by: list[str] = result.get("group_by") or []
    if not groups:
        return None
    column, first = next(iter(groups[0]["columns"].items()))
    notes = list(result.get("warnings") or [])
    if len(groups[0]["columns"]) > 1:
        notes.append(f"Only the first column ({column}) is charted.")
    if result.get("truncated"):
        notes.append(f"Showing {len(groups)} of {result['total_groups']} groups.")

    def key_of(group: dict[str, Any]) -> ChartValue:
        if not group_by:
            return "all rows"
        return " / ".join(str(_label(group["key"].get(k))) for k in group_by)

    x_label = " / ".join(group_by) if group_by else ""
    if first.get("proportions") is not None:
        # One series per category; with groups, stacked shares per group (sum to 1).
        categories: list[ChartValue] = []
        for g in groups:
            for p in g["columns"][column]["proportions"]:
                if p["value"] not in categories:
                    categories.append(p["value"])
        series = []
        for category in categories:
            ys, ns = [], []
            for g in groups:
                summary = g["columns"][column]
                share = next(
                    (p["proportion"] for p in summary["proportions"] if p["value"] == category),
                    0.0,
                )
                ys.append(share)
                ns.append(summary["n"])
            series.append(
                ChartSeries(name=str(category), x=[key_of(g) for g in groups], y=ys, n=ns)
            )
        if not group_by:  # single bar per category reads better than one stacked bar
            series = [
                ChartSeries(
                    name=column,
                    x=[s.name for s in series],
                    y=[s.y[0] for s in series],
                    n=[s.n[0] for s in series],
                )
            ]
        return ChartSpec(
            chart_type="bar",
            title=f"Share of {column}" + (f" by {x_label}" if group_by else ""),
            x_label=x_label or column,
            y_label="share of non-missing values",
            series=series,
            barmode="stack" if group_by else "group",
            y_format="percent",
            evidence_id=evidence_id,
            notes=notes,
        )

    metric = next((m for m in NUMERIC_ORDER if m in result.get("metrics", [])), None)
    if metric is None:
        return None
    return ChartSpec(
        chart_type="bar",
        title=f"{metric} of {column}" + (f" by {x_label}" if group_by else ""),
        x_label=x_label,
        y_label=f"{metric} {column}",
        series=[
            ChartSeries(
                name=f"{metric} {column}",
                x=[key_of(g) for g in groups],
                y=[g["columns"][column].get(metric) for g in groups],
                n=[g["columns"][column]["n"] for g in groups],
            )
        ],
        evidence_id=evidence_id,
        notes=notes,
    )


def _inspect_chart(evidence_id: str, result: dict[str, Any]) -> ChartSpec | None:
    columns = [c for c in result.get("columns", []) if c.get("n_missing", 0) > 0]
    if not columns:
        return None
    return ChartSpec(
        chart_type="bar",
        title="Missing values per column",
        x_label="column",
        y_label="missing values",
        series=[
            ChartSeries(
                name="missing",
                x=[c["name"] for c in columns],
                y=[float(c["n_missing"]) for c in columns],
                n=[result["row_count"]] * len(columns),
            )
        ],
        evidence_id=evidence_id,
        notes=["Columns without missing values are omitted."],
    )


def chart_for_evidence(
    tool_name: str, evidence_id: str, result: dict[str, Any]
) -> ChartSpec | None:
    if tool_name == "summarize":
        return _summarize_chart(evidence_id, result)
    if tool_name == "inspect_dataset":
        return _inspect_chart(evidence_id, result)
    return None


_OP_SYMBOLS = {
    "eq": "=",
    "ne": "≠",
    "lt": "<",
    "le": "≤",
    "gt": ">",
    "ge": "≥",
    "in": "in",
    "not_in": "not in",
    "is_missing": "is missing",
    "not_missing": "is not missing",
}


def describe_filters(filters: list[dict[str, Any]]) -> str:
    """Human-readable view definition, e.g. 'age > 40, sex = Female' ('all rows' if none)."""
    if not filters:
        return "all rows"
    parts = []
    for f in filters:
        value = f.get("value")
        shown = "" if value is None else f" {value}"
        parts.append(f"{f['column']} {_OP_SYMBOLS.get(f['op'], f['op'])}{shown}")
    return ", ".join(parts)


def claim_charts(cited: list[tuple[ChartSpec, str]]) -> list[ChartSpec]:
    """Charts for a claim from its cited evidence: (spec, view description) pairs.

    Single-value charts of the same measure (e.g. mean hours on 'sex = Male' and on
    'sex = Female') are merged into one comparison chart, labelled by view definition.
    Everything else is returned as is. Each merged bar keeps its source evidence ID.
    """
    single = [
        (spec, label)
        for spec, label in cited
        if len(spec.series) == 1 and len(spec.series[0].x) == 1
    ]
    by_measure: dict[tuple[str, str], list[tuple[ChartSpec, str]]] = {}
    for spec, label in single:
        by_measure.setdefault((spec.y_label, spec.y_format), []).append((spec, label))
    merged: list[ChartSpec] = []
    merged_ids: set[str] = set()
    for (y_label, y_format), group in by_measure.items():
        if len(group) < 2:
            continue
        merged_ids |= {spec.evidence_id for spec, _ in group}
        merged.append(
            ChartSpec(
                chart_type="bar",
                title=f"{y_label}: comparison across views",
                x_label="view",
                y_label=y_label,
                series=[
                    ChartSeries(
                        name=y_label,
                        x=[label for _, label in group],
                        y=[spec.series[0].y[0] for spec, _ in group],
                        n=[spec.series[0].n[0] for spec, _ in group],
                    )
                ],
                y_format=y_format,  # type: ignore[arg-type]
                evidence_id=",".join(spec.evidence_id for spec, _ in group),
                notes=["Merged from several cited evidence records (one bar each)."],
            )
        )
    rest = []
    for spec, label in cited:
        if spec.evidence_id in merged_ids:
            continue
        if len(spec.series) == 1 and spec.series[0].x == ["all rows"]:
            # An ungrouped summary of a filtered view: name the bar after the view.
            series = spec.series[0].model_copy(update={"x": [label]})
            spec = spec.model_copy(update={"series": [series], "x_label": "view"})
        rest.append(spec)
    return merged + rest
