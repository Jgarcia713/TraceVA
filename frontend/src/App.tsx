import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChartSpec, type DemoInfo, type GraphOut, type MessageOut, type RunDetail } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { ClaimInspector } from "./components/ClaimInspector";
import { ProvenanceGraph } from "./components/ProvenanceGraph";

const POLL_MS = 600;
const MAX_POLL_DELAY_MS = 8000;
const MAX_POLL_FAILURES = 10; // about a minute of retries with backoff
const RETRY_PREFIX = "Connection problem";

function errorMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.body.code}: ${e.body.message}` : e instanceof Error ? e.message : String(e);
}

export function App() {
  const [demo, setDemo] = useState<DemoInfo | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [runs, setRuns] = useState<Record<string, RunDetail>>({});
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [atSequence, setAtSequence] = useState<number | null>(null);
  const [charts, setCharts] = useState<ChartSpec[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Single polite live region for meaningful completions (not "loading" states).
  const [announcement, setAnnouncement] = useState("");

  // The poll loop must see the *current* selection, not the one captured when it started.
  const selectedRunRef = useRef<string | null>(null);
  useEffect(() => {
    selectedRunRef.current = selectedRunId;
  }, [selectedRunId]);

  const startConversation = useCallback(async () => {
    try {
      const conversation = await api.createConversation();
      setConversationId(conversation.id);
      setMessages([]);
      setRuns({});
      setSelectedRunId(null);
      setSelectedClaimId(null);
      setGraph(null);
      setCharts(null);
      setError(null);
    } catch (e) {
      setError(`Could not start a conversation (${errorMessage(e)}). Is the backend running?`);
    }
  }, []);

  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current) return; // StrictMode runs effects twice in development
    initialized.current = true;
    api.demo().then(setDemo).catch(() => setDemo(null));
    void startConversation();
  }, [startConversation]);

  const refreshMessages = useCallback(async (conversation: string) => {
    setMessages((await api.messages(conversation)).items);
  }, []);

  // Poll the active run until it finishes. Network errors are retried with backoff
  // instead of silently stopping (which would leave the UI busy forever).
  useEffect(() => {
    if (!activeRunId || !conversationId) return;
    let cancelled = false;
    let timer: number | undefined;
    let failures = 0;
    const schedule = (ms: number) => {
      timer = window.setTimeout(() => void tick(), ms);
    };
    const tick = async () => {
      try {
        const run = await api.run(activeRunId);
        if (cancelled) return;
        setRuns((r) => ({ ...r, [run.id]: run }));
        if (selectedRunRef.current === run.id) {
          // Only the run being viewed may replace the graph.
          const live = await api.graph(run.id);
          if (!cancelled && selectedRunRef.current === run.id) setGraph(live);
        }
        if (failures > 0) setError((prev) => (prev?.startsWith(RETRY_PREFIX) ? null : prev));
        failures = 0;
        if (run.status === "running") {
          schedule(POLL_MS);
          return;
        }
        setActiveRunId(null);
        setAnnouncement(
          run.status === "completed"
            ? `Answer ready with ${run.claims.length} claim${run.claims.length === 1 ? "" : "s"}.`
            : `Analysis failed: ${run.error?.code ?? "unknown error"}.`,
        );
        if (selectedRunRef.current === run.id) setSelectedClaimId(run.claims[0]?.id ?? null);
        try {
          await refreshMessages(conversationId);
        } catch (e) {
          setError(`Could not refresh messages (${errorMessage(e)}).`);
        }
      } catch (e) {
        if (cancelled) return;
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          setActiveRunId(null);
          setError(`Lost contact with the backend while a run was active (${errorMessage(e)}). Reload to see its final status.`);
          return;
        }
        setError(`${RETRY_PREFIX} (${errorMessage(e)}); retrying…`);
        schedule(Math.min(POLL_MS * 2 ** failures, MAX_POLL_DELAY_MS));
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRunId, conversationId, refreshMessages]);

  // Selected run (not the live one): its graph, at a snapshot if the slider is moved.
  useEffect(() => {
    if (!selectedRunId || activeRunId === selectedRunId) return;
    let cancelled = false; // ignore responses for a selection the user already left
    api
      .graph(selectedRunId, atSequence ?? undefined)
      .then((g) => !cancelled && setGraph(g))
      .catch(() => !cancelled && setGraph(null));
    return () => {
      cancelled = true;
    };
  }, [selectedRunId, atSequence, activeRunId]);

  // Selected claim: its charts (backend chart specs from the cited evidence).
  useEffect(() => {
    setCharts(null);
    if (!selectedClaimId) return;
    let cancelled = false;
    api
      .claimCharts(selectedClaimId)
      .then((r) => !cancelled && setCharts(r.charts))
      .catch(() => !cancelled && setCharts([]));
    return () => {
      cancelled = true;
    };
  }, [selectedClaimId]);

  const ask = async (question: string) => {
    if (!conversationId) {
      setError("No conversation yet: the backend may be unavailable. Try “New conversation”.");
      return;
    }
    setError(null);
    let runId: string;
    try {
      runId = (await api.ask(conversationId, question)).run_id;
    } catch (e) {
      setError(errorMessage(e));
      return;
    }
    // Start polling first: the run executes on the server whatever happens below.
    setSelectedRunId(runId);
    setSelectedClaimId(null);
    setAtSequence(null);
    setActiveRunId(runId);
    try {
      await refreshMessages(conversationId);
    } catch (e) {
      setError(`Could not refresh messages (${errorMessage(e)}).`);
    }
  };

  const selectClaim = (runId: string, claimId: string) => {
    setSelectedRunId(runId);
    setSelectedClaimId(claimId);
    setAtSequence(null);
  };

  const selectedRun = selectedRunId ? runs[selectedRunId] : null;
  const selectedClaim = selectedRun?.claims.find((c) => c.id === selectedClaimId) ?? null;
  const createdSeqs = graph?.nodes.map((n) => n.created_seq).filter((s): s is number => s !== null) ?? [];
  const sequenceRange: [number, number] | null =
    selectedRun?.last_event_sequence && createdSeqs.length && activeRunId !== selectedRunId
      ? [Math.min(...createdSeqs), selectedRun.last_event_sequence]
      : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>TraceVA</h1>
          <span>explainable agentic visual analytics · prototype</span>
        </div>
        {demo?.enabled && <div className="demo-notice">{demo.notice}</div>}
      </header>
      <div className="visually-hidden" aria-live="polite">
        {announcement}
      </div>
      <main className="layout">
        <ChatPanel
          messages={messages}
          runs={runs}
          activeRunId={activeRunId}
          selectedClaimId={selectedClaimId}
          demo={demo}
          error={error}
          onAsk={ask}
          onSelectClaim={selectClaim}
          onNewConversation={() => void startConversation()}
        />
        {selectedRun ? (
          <ClaimInspector run={selectedRun} claim={selectedClaim} charts={charts} graph={graph} />
        ) : (
          <section className="panel inspector">
            <header className="panel-header">
              <h2>Claim and evidence</h2>
            </header>
            <p className="empty">Answers appear here with their charts, evidence, and assumptions.</p>
          </section>
        )}
        <ProvenanceGraph
          graph={graph}
          selectedClaimId={selectedClaimId}
          sequenceRange={sequenceRange}
          atSequence={atSequence}
          onSequence={setAtSequence}
          onSelectNode={(id, type) => {
            if (type === "claim" && selectedRunId) selectClaim(selectedRunId, id);
          }}
        />
      </main>
    </div>
  );
}
