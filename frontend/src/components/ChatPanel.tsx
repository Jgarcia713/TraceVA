import { useEffect, useRef, useState } from "react";
import type { DemoInfo, MessageOut, RunDetail } from "../api";

interface Props {
  messages: MessageOut[];
  runs: Record<string, RunDetail>;
  activeRunId: string | null;
  selectedClaimId: string | null;
  demo: DemoInfo | null;
  error: string | null;
  onAsk: (question: string) => void;
  onSelectClaim: (runId: string, claimId: string) => void;
  onNewConversation: () => void;
}

export function ChatPanel(props: Props) {
  const { messages, runs, activeRunId, demo } = props;
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const progress = activeRunId ? runs[activeRunId]?.operations.length ?? 0 : 0;
  useEffect(() => {
    // Keep the newest message (or live progress) in view.
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, activeRunId, progress]);
  const busy = activeRunId !== null;
  const asked = new Set(messages.filter((m) => m.role === "user").map((m) => m.content));

  const send = (text: string) => {
    if (!text.trim() || busy) return;
    props.onAsk(text.trim());
    setDraft("");
  };

  return (
    <section className="panel chat">
      <header className="panel-header">
        <h2>Conversation</h2>
        <button className="link" onClick={props.onNewConversation} disabled={busy}>
          New conversation
        </button>
      </header>

      <div className="messages" ref={scroller}>
        {messages.length === 0 && (
          <p className="empty">Ask a question about the Adult Income dataset (32,561 rows).</p>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="msg msg-user">
              {m.content}
            </div>
          ) : (
            <AnswerBubble key={m.id} message={m} run={m.run_id ? runs[m.run_id] : undefined} {...props} />
          ),
        )}
        {activeRunId && runs[activeRunId] && <Progress run={runs[activeRunId]} />}
        {messages
          .filter((m) => m.role === "user" && m.run_id && runs[m.run_id]?.status === "failed")
          .map((m) => (
            <div key={`fail-${m.id}`} className="msg msg-failed">
              Run failed: <strong>{runs[m.run_id!].error?.code}</strong> — {runs[m.run_id!].error?.message}
            </div>
          ))}
      </div>

      {props.error && (
        <div className="error-banner" role="alert">
          {props.error}
        </div>
      )}

      {demo?.enabled && (
        <div className="demo-questions">
          <div className="demo-label">Recorded questions (demo mode)</div>
          {demo.questions.map((q) => (
            <button
              key={q.question}
              className={`chip ${asked.has(q.question) ? "chip-done" : ""}`}
              disabled={busy}
              onClick={() => send(q.question)}
              aria-description={`Recorded with ${q.original_model}, prompt ${q.prompt_version}; originally took ${q.recorded_wall_s ?? "?"} s`}
            >
              {q.question}
              <span className="chip-meta">originally {Math.round(q.recorded_wall_s ?? 0)} s</span>
            </button>
          ))}
        </div>
      )}

      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <label htmlFor="question" className="visually-hidden">
          Question about the dataset
        </label>
        <input
          id="question"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={busy ? "Analysis running…" : "e.g. Mean hours for people over 40?"}
          disabled={busy}
          autoComplete="off"
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}

function AnswerBubble({
  message,
  run,
  selectedClaimId,
  onSelectClaim,
}: { message: MessageOut; run?: RunDetail } & Props) {
  const [explanation, caveats] = message.content.split("\n\nCaveats:\n");
  return (
    <div className="msg msg-assistant">
      <div className="answer-head">
        <span className="badge badge-unverified" title="Model-written text; only claims are tied to evidence">
          explanation not verified
        </span>
      </div>
      <p className="answer-text">{explanation}</p>
      {message.unverified_numbers && message.unverified_numbers.length > 0 && (
        <p className="loose-numbers">
          Numbers not tied to any claim: {message.unverified_numbers.join(", ")}
        </p>
      )}
      {caveats && (
        <ul className="caveats">
          {caveats
            .split("\n")
            .filter(Boolean)
            .map((c) => (
              <li key={c}>{c.replace(/^- /, "")}</li>
            ))}
        </ul>
      )}
      {run && run.claims.length > 0 && (
        <div className="claims">
          <div className="claims-label">Claims (cited evidence)</div>
          {run.claims.map((claim) => (
            <button
              key={claim.id}
              className={`claim-card ${selectedClaimId === claim.id ? "selected" : ""}`}
              onClick={() => onSelectClaim(run.id, claim.id)}
            >
              <span className="claim-text">{claim.text}</span>
              <span className="claim-meta">
                {claim.evidence_ids.length} evidence ·{" "}
                {claim.assumptions.stated.length + claim.assumptions.derived.length} assumptions
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Progress({ run }: { run: RunDetail }) {
  return (
    <div className="msg msg-progress">
      <div className="spinner-row">
        {/* Native indeterminate <progress>: announced as busy without a live region. */}
        <progress className="loading-spinner" aria-label="Analysis running" /> Analysis running…
      </div>
      <ol className="progress-ops">
        {run.operations.map((op) => (
          <li key={op.id} className={op.status}>
            {op.status === "succeeded" ? "✓" : "✗"} {op.tool_name}
            {op.error && <span className="op-error"> — {op.error.code}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
