"""Pure provenance helpers: explanation number check and method-template caveats."""

from __future__ import annotations

from app.provenance.templates import method_caveats
from app.provenance.text_checks import explanation_part, numbers_in, unverified_numbers


def test_numbers_are_normalized() -> None:
    assert numbers_in("21,790 men, 42.40 hours, 6 groups, 20.9%") == {"21790", "42.4", "6", "20.9"}


def test_unverified_numbers_lists_only_numbers_absent_from_claims() -> None:
    explanation = "Men work 42.43 hours vs 36.41 for women, a gap of 6.02 hours; 15 columns."
    claims = ["Men average 42.43 hours per week", "Women average 36.41 hours"]
    assert unverified_numbers(explanation, claims) == ["6.02", "15"]
    assert unverified_numbers("No numbers here.", claims) == []


def test_identifiers_are_not_numbers() -> None:
    assert numbers_in("see ev_2e22 and view_5b0e") == set()


def test_summarize_templates() -> None:
    result = {
        "truncated": True,
        "total_groups": 73,
        "groups": [
            {"n_rows": 10, "columns": {"age": {"n": 9, "n_missing": 1}}},
            {"n_rows": 500, "columns": {"age": {"n": 500, "n_missing": 0}}},
        ],
    }
    keys = [
        k for k, _ in method_caveats("summarize", {"metrics": ["mean"], "group_by": ["x"]}, result)
    ]
    assert keys == ["mean_sensitivity", "groups_truncated", "missing_in_summary", "small_groups"]
    assert (
        method_caveats("summarize", {"metrics": ["median"]}, {"truncated": False, "groups": []})
        == []
    )


def test_create_view_templates() -> None:
    result = {
        "row_count": 12,
        "applied": [
            {"column": "workclass", "op": "ne", "value": "Private", "rows_excluded_missing": 9},
            {"column": "age", "op": "gt", "value": 40, "rows_excluded_missing": 0},
        ],
    }
    caveats = method_caveats("create_view", {}, result)
    assert [k for k, _ in caveats] == ["missing_excluded", "small_view"]
    assert "9 rows with missing 'workclass'" in caveats[0][1]
    assert method_caveats("inspect_dataset", {}, {}) == []


def test_check_ignores_appended_caveats() -> None:
    stored = "Men work more.\n\nCaveats:\n- Groups under 30 rows are unstable."
    assert explanation_part(stored) == "Men work more."
    assert unverified_numbers(explanation_part(stored), []) == []
