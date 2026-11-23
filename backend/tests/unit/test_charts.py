"""Evidence -> chart mapping (pure)."""

from __future__ import annotations

from typing import Any

import pytest

from app.analysis.charts import chart_for_evidence, claim_charts, describe_filters


def group(key: dict[str, Any], n: int, **summary: Any) -> dict[str, Any]:
    return {"key": key, "n_rows": n, "columns": {"c": {"n": n, "n_missing": 0, **summary}}}


def test_grouped_mean_is_one_bar_per_group_with_denominators() -> None:
    result = {
        "group_by": ["sex"],
        "metrics": ["mean"],
        "truncated": False,
        "total_groups": 3,
        "groups": [
            group({"sex": "M"}, 10, mean=42.0),
            group({"sex": "F"}, 5, mean=36.0),
            group({"sex": None}, 2, mean=40.0),
        ],
    }
    spec = chart_for_evidence("summarize", "ev_1", result)
    assert spec is not None and spec.evidence_id == "ev_1"
    (series,) = spec.series
    assert series.x == ["M", "F", "(missing)"]  # missing key group is labelled, not dropped
    assert series.y == [42.0, 36.0, 40.0] and series.n == [10, 5, 2]
    assert spec.y_format == "number"


def test_grouped_proportions_are_stacked_shares() -> None:
    def props(**shares: float) -> list[dict[str, Any]]:
        return [{"value": k, "count": 0, "proportion": v} for k, v in shares.items()]

    result = {
        "group_by": ["edu"],
        "metrics": ["proportions"],
        "truncated": True,
        "total_groups": 16,
        "groups": [
            group({"edu": "BA"}, 100, proportions=props(low=0.6, high=0.4)),
            group({"edu": "HS"}, 50, proportions=props(low=0.9)),
        ],
    }
    spec = chart_for_evidence("summarize", "ev_2", result)
    assert spec is not None and spec.barmode == "stack" and spec.y_format == "percent"
    names = [s.name for s in spec.series]
    assert names == ["low", "high"]
    high = spec.series[1]
    assert high.y == [0.4, 0.0]  # absent category is an explicit zero share
    assert any("Showing 2 of 16" in note for note in spec.notes)


def test_ungrouped_proportions_are_bars_per_category() -> None:
    result = {
        "group_by": [],
        "metrics": ["proportions"],
        "truncated": False,
        "total_groups": 1,
        "groups": [
            group(
                {},
                20,
                proportions=[
                    {"value": "<=50K", "count": 15, "proportion": 0.75},
                    {"value": ">50K", "count": 5, "proportion": 0.25},
                ],
            )
        ],
    }
    spec = chart_for_evidence("summarize", "ev_3", result)
    assert spec is not None
    (series,) = spec.series
    assert series.x == ["<=50K", ">50K"] and series.y == [0.75, 0.25]


def test_inspect_chart_and_no_chart_cases() -> None:
    inspect = {
        "row_count": 100,
        "columns": [{"name": "a", "n_missing": 3}, {"name": "b", "n_missing": 0}],
    }
    spec = chart_for_evidence("inspect_dataset", "ev_4", inspect)
    assert spec is not None and spec.series[0].x == ["a"]
    assert chart_for_evidence("create_view", "ev_5", {"view_id": "v"}) is None
    assert chart_for_evidence("summarize", "ev_6", {"groups": []}) is None


def test_describe_filters() -> None:
    assert describe_filters([]) == "all rows"
    assert describe_filters(
        [
            {"column": "age", "op": "gt", "value": 40},
            {"column": "sex", "op": "eq", "value": "Female"},
        ]
    ) == ("age > 40, sex = Female")
    assert describe_filters([{"column": "w", "op": "is_missing", "value": None}]) == "w is missing"


def test_claim_charts_merge_single_value_comparisons() -> None:
    def one_bar(ev: str, y: float, n: int) -> Any:
        return chart_for_evidence(
            "summarize",
            ev,
            {
                "group_by": [],
                "metrics": ["mean"],
                "truncated": False,
                "total_groups": 1,
                "groups": [group({}, n, mean=y)],
            },
        )

    men, women = one_bar("ev_m", 42.4, 21790), one_bar("ev_w", 36.4, 10771)
    assert men is not None and women is not None
    (merged,) = claim_charts([(men, "sex = Male"), (women, "sex = Female")])
    assert merged.series[0].x == ["sex = Male", "sex = Female"]
    assert merged.series[0].y == [pytest.approx(42.4), pytest.approx(36.4)]
    assert merged.evidence_id == "ev_m,ev_w"
    assert claim_charts([(men, "sex = Male")]) == [men]  # a single chart is kept as is
