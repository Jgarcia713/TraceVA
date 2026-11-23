// Small presentation helpers shared by components.

import type { GraphOut, OperationOut } from "./api";

const OP_SYMBOLS: Record<string, string> = {
  eq: "=", ne: "≠", lt: "<", le: "≤", gt: ">", ge: "≥",
  in: "in", not_in: "not in", is_missing: "is missing", not_missing: "is not missing",
};

export function describeFilters(filters: unknown): string {
  if (!Array.isArray(filters) || filters.length === 0) return "all rows";
  return filters
    .map((f: { column: string; op: string; value?: unknown }) => {
      const value = f.value === null || f.value === undefined ? "" : ` ${JSON.stringify(f.value)}`;
      return `${f.column} ${OP_SYMBOLS[f.op] ?? f.op}${value}`.replace(/"/g, "");
    })
    .join(", ");
}

/** "summarize(hours_per_week · mean · by sex) on [age > 40]" */
export function describeOperation(op: OperationOut, graph?: GraphOut | null): string {
  const args = op.args ?? safeParse(op.raw_arguments);
  const viewId = typeof args?.view_id === "string" ? args.view_id : undefined;
  const viewNode = viewId ? graph?.nodes.find((n) => n.id === viewId) : undefined;
  const on = viewNode ? ` on [${describeFilters(viewNode.data.filters)}]` : "";
  if (op.tool_name === "summarize" && args) {
    const by = Array.isArray(args.group_by) && args.group_by.length ? ` · by ${args.group_by.join(", ")}` : "";
    return `summarize(${asList(args.columns)} · ${asList(args.metrics)}${by})${on}`;
  }
  if (op.tool_name === "create_view" && args) {
    return `create_view(${describeFilters(args.filters)})${on}`;
  }
  if (op.tool_name === "inspect_dataset") return "inspect_dataset()";
  return `${op.tool_name}(${op.raw_arguments.slice(0, 60)})`;
}

function asList(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

function safeParse(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function shortId(id: string): string {
  const [prefix, rest] = id.split("_");
  return rest ? `${prefix}_${rest.slice(0, 6)}` : id;
}
