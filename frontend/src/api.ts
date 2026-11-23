// Typed client for the TraceVA backend (/api/v1). Types mirror backend/app/schemas/api.py.

export type RunStatus = "running" | "completed" | "failed";

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
  request_id: string;
}

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

export interface MessageOut {
  id: string;
  conversation_id: string;
  run_id: string | null;
  role: "user" | "assistant";
  content: string;
  sequence_no: number;
  created_at: string;
  verified: boolean | null;
  unverified_numbers: string[] | null;
}

export interface AssumptionOut {
  id: string;
  text: string;
  source: "agent_stated" | "method_template";
  status: string;
  template_key: string | null;
  scope: "claim" | "answer" | null;
  operations: string[] | null;
}

export interface ClaimOut {
  id: string;
  sequence_no: number;
  text: string;
  status: string;
  evidence_ids: string[];
  citation_origin: "agent_proposed";
  assumptions: { stated: AssumptionOut[]; derived: AssumptionOut[] };
}

export interface OperationOut {
  id: string;
  sequence_no: number;
  tool_name: string;
  status: "succeeded" | "failed";
  raw_arguments: string;
  args: Record<string, unknown> | null;
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
  input_ids: string[];
  output_ids: string[];
  evidence_id: string | null;
}

export interface RunDetail {
  id: string;
  conversation_id: string;
  status: RunStatus;
  created_at: string;
  completed_at: string | null;
  error: { code: string; message: string } | null;
  usage: Record<string, number> | null;
  config: { prompt_version?: string; llm?: Record<string, unknown> };
  question: MessageOut | null;
  answer: MessageOut | null;
  operations: OperationOut[];
  claims: ClaimOut[];
  assumptions: AssumptionOut[];
  last_event_sequence: number | null;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  created_seq: number | null;
  data: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  origin: string;
}

export interface GraphOut {
  run_id: string;
  at_sequence: number | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ChartSeries {
  name: string;
  x: (string | number | null)[];
  y: (number | null)[];
  n: number[];
}

export interface ChartSpec {
  chart_type: "bar";
  title: string;
  x_label: string;
  y_label: string;
  series: ChartSeries[];
  barmode: "group" | "stack";
  y_format: "number" | "percent";
  evidence_id: string;
  notes: string[];
}

export interface DemoInfo {
  enabled: boolean;
  notice: string | null;
  questions: {
    question: string;
    original_model: string;
    prompt_version: string;
    recorded_wall_s: number | null;
    recorded_model_calls: number | null;
    recorded_failed_operations: number | null;
  }[];
}

export interface EvidenceOut {
  id: string;
  operation_id: string;
  run_id: string;
  tool_name: string;
  result: Record<string, unknown>;
}

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  // A down backend reaches us as the dev proxy's 502/504 with an HTML or empty body:
  // never assume JSON, and never assume the error envelope is present.
  const isJson = response.headers.get("content-type")?.includes("application/json") ?? false;
  const body: unknown = isJson ? await response.json().catch(() => null) : null;
  if (!response.ok) {
    const envelope = (body as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(
      response.status,
      envelope ?? {
        code: `HTTP_${response.status}`,
        message: response.status >= 502 ? "backend unavailable" : response.statusText || "request failed",
        request_id: "",
      },
    );
  }
  if (body === null) throw new ApiError(response.status, { code: "BAD_RESPONSE", message: "expected JSON", request_id: "" });
  return body as T;
}

export const api = {
  demo: () => request<DemoInfo>("/demo"),
  createConversation: () => request<{ id: string }>("/conversations", { method: "POST" }),
  messages: (conversationId: string) =>
    request<{ items: MessageOut[] }>(`/conversations/${conversationId}/messages?limit=500`),
  ask: (conversationId: string, content: string) =>
    request<{ run_id: string; message_id: string }>(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  run: (runId: string) => request<RunDetail>(`/runs/${runId}`),
  graph: (runId: string, atSequence?: number) =>
    request<GraphOut>(
      `/runs/${runId}/graph${atSequence === undefined ? "" : `?at_sequence=${atSequence}`}`,
    ),
  claimCharts: (claimId: string) =>
    request<{ claim_id: string; charts: ChartSpec[] }>(`/claims/${claimId}/charts`),
  evidence: (evidenceId: string) => request<EvidenceOut>(`/evidence/${evidenceId}`),
};
