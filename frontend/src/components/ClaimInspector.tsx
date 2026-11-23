import { useState } from "react";
import { api, type AssumptionOut, type ChartSpec, type ClaimOut, type GraphOut, type RunDetail } from "../api";
import { describeOperation, shortId } from "../format";
import { ChartView } from "./ChartView";

interface Props {
  run: RunDetail;
  claim: ClaimOut | null;
  charts: ChartSpec[] | null;
  graph: GraphOut | null;
}

export function ClaimInspector({ run, claim, charts, graph }: Props) {
  return (
    <section className="panel inspector">
      <header className="panel-header">
        <h2>Claim and evidence</h2>
        <RunMeta run={run} />
      </header>

      {!claim ? (
        <p className="empty">
          {run.status === "completed" ? "This answer made no claims." : "Select a claim to inspect it."}
        </p>
      ) : (
        <>
          <blockquote className="claim-focus">
            {claim.text}
            <span className="badge badge-agent" title="The model chose which evidence to cite; the backend checked it exists in this run">
              agent-proposed citation
            </span>
          </blockquote>

          {charts === null ? (
            <p className="empty">Loading chart…</p>
          ) : charts.length === 0 ? (
            <p className="empty">The cited evidence has no chart (e.g. a filter definition).</p>
          ) : (
            charts.map((spec) => <ChartView key={spec.evidence_id + spec.title} spec={spec} />)
          )}

          <h3>Cited evidence</h3>
          <ul className="evidence-list">
            {claim.evidence_ids.map((id) => {
              const op = run.operations.find((o) => o.evidence_id === id);
              return (
                <EvidenceItem key={id} id={id} label={op ? describeOperation(op, graph) : "?"} />
              );
            })}
          </ul>

          <h3>Assumptions bearing on this claim</h3>
          {claim.assumptions.stated.length + claim.assumptions.derived.length === 0 ? (
            <p className="empty">None stated or derived.</p>
          ) : (
            <ul className="assumptions">
              {claim.assumptions.stated.map((a) => (
                <AssumptionItem key={a.id} a={a} kind={a.scope === "claim" ? "stated for this claim" : "stated for the whole answer"} />
              ))}
              {claim.assumptions.derived.map((a) => (
                <AssumptionItem key={a.id} a={a} kind="method caveat (backend, via lineage)" />
              ))}
            </ul>
          )}
        </>
      )}

      <h3>How the agent got there</h3>
      <ol className="trace">
        {run.operations.map((op) => {
          const repaired =
            op.status === "failed" &&
            run.operations.some((o) => o.tool_name === op.tool_name && o.status === "succeeded" && o.sequence_no > op.sequence_no);
          return (
            <li key={op.id} className={op.status}>
              <span className="trace-status">{op.status === "succeeded" ? "✓" : "✗"}</span>
              <span className="trace-call">{describeOperation(op, graph)}</span>
              {op.error && (
                <span className="trace-error">
                  {op.error.code}: {op.error.message.slice(0, 110)}
                  {repaired && <span className="badge badge-ok">repaired next</span>}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RunMeta({ run }: { run: RunDetail }) {
  const llm = run.config.llm ?? {};
  const replay = llm.provider === "replay";
  return (
    <div className="run-meta">
      <span className={`status status-${run.status}`}>{run.status}</span>
      <span>
        {replay ? `replay of ${String(llm.original_model)}` : String(llm.model ?? "")} · prompt{" "}
        {String(replay ? llm.original_prompt_version : run.config.prompt_version)}
      </span>
      {run.usage && <span>{(run.usage.wall_ms / 1000).toFixed(1)} s · {run.usage.model_calls} model calls</span>}
    </div>
  );
}

function EvidenceItem({ id, label }: { id: string; label: string }) {
  const [raw, setRaw] = useState<string | null>(null);
  return (
    <li>
      <code>{shortId(id)}</code> {label}
      <button
        className="link"
        onClick={async () => {
          if (raw) return setRaw(null);
          const ev = await api.evidence(id);
          setRaw(JSON.stringify(ev.result, null, 2));
        }}
      >
        {raw ? "hide" : "raw result"}
      </button>
      {raw && <pre className="raw">{raw}</pre>}
    </li>
  );
}

function AssumptionItem({ a, kind }: { a: AssumptionOut; kind: string }) {
  return (
    <li className={`assumption assumption-${a.source}`}>
      <span className="assumption-kind">{kind}</span>
      <span>{a.text}</span>
      <span className="badge badge-untested">{a.status.replace("_", " ")}</span>
    </li>
  );
}
