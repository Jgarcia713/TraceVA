import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChartSpec, type DemoInfo, type GraphOut, type MessageOut, type RunDetail } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { ClaimInspector } from "./components/ClaimInspector";
import { ProvenanceGraph } from "./components/ProvenanceGraph";

const POLL_MS = 600;

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
  const pollTimer = useRef<number | null>(null);

  const startConversation = useCallback(async () => {
    const conversation = await api.createConversation();
    setConversationId(conversation.id);
    setMessages([]);
    setRuns({});
    setSelectedRunId(null);
    setSelectedClaimId(null);
    setGraph(null);
    setCharts(null);
    setError(null);
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

  // Poll the active run until it finishes; keep its graph live while it grows.
  useEffect(() => {
    if (!activeRunId || !conversationId) return;
    const tick = async () => {
      const run = await api.run(activeRunId);
      setRuns((r) => ({ ...r, [run.id]: run }));
      setGraph(await api.graph(run.id));
      if (run.status === "running") {
        pollTimer.current = window.setTimeout(tick, POLL_MS);
        return;
      }
      setActiveRunId(null);
      await refreshMessages(conversationId);
      setSelectedClaimId(run.claims[0]?.id ?? null);
    };
    void tick();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [activeRunId, conversationId, refreshMessages]);

  // Selected run: (re)load its graph, at a snapshot if the slider is moved.
  useEffect(() => {
    if (!selectedRunId || activeRunId === selectedRunId) return;
    api.graph(selectedRunId, atSequence ?? undefined).then(setGraph).catch(() => setGraph(null));
  }, [selectedRunId, atSequence, activeRunId]);

  // Selected claim: its charts (backend chart specs from the cited evidence).
  useEffect(() => {
    if (!selectedClaimId) {
      setCharts(null);
      return;
    }
    setCharts(null);
    api.claimCharts(selectedClaimId).then((r) => setCharts(r.charts)).catch(() => setCharts([]));
  }, [selectedClaimId]);

  const ask = async (question: string) => {
    if (!conversationId) return;
    setError(null);
    try {
      const accepted = await api.ask(conversationId, question);
      setSelectedRunId(accepted.run_id);
      setSelectedClaimId(null);
      setAtSequence(null);
      await refreshMessages(conversationId);
      setActiveRunId(accepted.run_id);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.body.code}: ${e.body.message}` : String(e));
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
    selectedRun?.last_event_sequence && createdSeqs.length && !activeRunId
      ? [Math.min(...createdSeqs), selectedRun.last_event_sequence]
      : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>TraceVA</strong>
          <span>explainable agentic visual analytics · prototype</span>
        </div>
        {demo?.enabled && <div className="demo-notice">{demo.notice}</div>}
      </header>
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
