"""Method-template caveats: backend-generated, derived only from a tool's own result.

These are stated caveats about the method, not tested assumptions (brief invariant 7).
Pure functions: (tool name, validated args, full result) -> [(template_key, text)].
"""

from __future__ import annotations

from typing import Any

SMALL_GROUP = 30


def _summarize(args: dict[str, Any], result: dict[str, Any]) -> list[tuple[str, str]]:
    caveats: list[tuple[str, str]] = []
    if "mean" in args.get("metrics", []):
        caveats.append(
            (
                "mean_sensitivity",
                "The mean is sensitive to skew and outliers; the median is more robust.",
            )
        )
    if result.get("truncated"):
        caveats.append(
            (
                "groups_truncated",
                f"Only the {len(result['groups'])} largest of "
                f"{result['total_groups']} groups were returned.",
            )
        )
    missing_cols = sorted(
        {
            col
            for group in result.get("groups", [])
            for col, summary in group["columns"].items()
            if summary.get("n_missing", 0) > 0
        }
    )
    if missing_cols:
        caveats.append(
            (
                "missing_in_summary",
                f"Missing values in {', '.join(missing_cols)} are excluded "
                "from the statistics; denominators count non-missing values only.",
            )
        )
    if args.get("group_by") and any(g["n_rows"] < SMALL_GROUP for g in result.get("groups", [])):
        caveats.append(
            (
                "small_groups",
                f"Some groups have fewer than {SMALL_GROUP} rows; their "
                "statistics may be unstable.",
            )
        )
    return caveats


def _create_view(args: dict[str, Any], result: dict[str, Any]) -> list[tuple[str, str]]:
    caveats: list[tuple[str, str]] = []
    for f in result.get("applied", []):
        n = f.get("rows_excluded_missing", 0)
        if n > 0:
            caveats.append(
                (
                    "missing_excluded",
                    f"{n} rows with missing '{f['column']}' are excluded by "
                    f"the filter {f['column']} {f['op']} {f.get('value')}; results describe rows "
                    "with known values only.",
                )
            )
    if 0 < result.get("row_count", 0) < SMALL_GROUP:
        caveats.append(
            (
                "small_view",
                f"The filtered view has only {result['row_count']} rows; "
                "statistics may be unstable.",
            )
        )
    return caveats


_TEMPLATES = {"summarize": _summarize, "create_view": _create_view}


def method_caveats(
    tool_name: str, args: dict[str, Any], result: dict[str, Any]
) -> list[tuple[str, str]]:
    template = _TEMPLATES.get(tool_name)
    return template(args, result) if template else []
