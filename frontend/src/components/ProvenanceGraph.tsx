import { useMemo } from "react";
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphEdge, GraphOut } from "../api";

// Left-to-right lineage: data -> computation -> evidence -> claims -> answer.
const COLUMN: Record<string, number> = {
  source: 0,
  view: 1,
  user_message: 2,
  run: 2,
  operation: 3,
  evidence: 4,
  claim: 5,
  assumption: 6,
  assistant_message: 6,
};
const COLOR: Record<string, string> = {
  source: "#5b6574",
  view: "#2f9e72",
  user_message: "#3b6fd8",
  run: "#3b6fd8",
  operation: "#7a5fc9",
  evidence: "#e08a2c",
  claim: "#b5487f",
  assumption: "#8a8f98",
  assistant_message: "#3b6fd8",
};
const EDGE_STYLE: Record<string, { stroke: string; strokeDasharray?: string }> = {
  backend_verified: { stroke: "#5b6574" },
  agent_proposed: { stroke: "#b5487f", strokeDasharray: "6 4" },
  agent_stated: { stroke: "#8a8f98", strokeDasharray: "2 4" },
  method_template: { stroke: "#2f9e72", strokeDasharray: "2 4" },
};

/** Upstream lineage of a claim: cited evidence, producing operations, consumed views and
 *  their ancestry, plus assumptions that qualify any of those nodes. */
export function claimLineage(graph: GraphOut, claimId: string): Set<string> {
  const keep = new Set<string>([claimId]);
  const incoming = (target: string, types: string[]) =>
    graph.edges.filter((e) => e.target === target && types.includes(e.type));
  const outgoing = (source: string, types: string[]) =>
    graph.edges.filter((e) => e.source === source && types.includes(e.type));
  const queue = [claimId];
  while (queue.length) {
    const id = queue.pop()!;
    const next: GraphEdge[] = [
      ...incoming(id, ["SUPPORTS", "PRODUCES"]).map((e) => ({ ...e, target: e.source })),
      ...outgoing(id, ["CONSUMES", "DERIVED_FROM"]),
    ];
    for (const e of next) {
      if (!keep.has(e.target)) {
        keep.add(e.target);
        queue.push(e.target);
      }
    }
  }
  for (const e of graph.edges) {
    if (e.type === "QUALIFIES" && keep.has(e.target)) keep.add(e.source);
  }
  return keep;
}

interface Props {
  graph: GraphOut | null;
  selectedClaimId: string | null;
  sequenceRange: [number, number] | null;
  atSequence: number | null;
  onSequence: (seq: number | null) => void;
  onSelectNode: (nodeId: string, type: string) => void;
}

export function ProvenanceGraph({ graph, selectedClaimId, sequenceRange, atSequence, onSequence, onSelectNode }: Props) {
  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [] as Node[], edges: [] as Edge[] };
    const lineage = selectedClaimId && graph.nodes.some((n) => n.id === selectedClaimId)
      ? claimLineage(graph, selectedClaimId)
      : null;
    const rows: Record<number, number> = {};
    const nodes: Node[] = graph.nodes.map((n) => {
      const col = COLUMN[n.type] ?? 7;
      const row = (rows[col] = (rows[col] ?? -1) + 1);
      const dim = lineage !== null && !lineage.has(n.id);
      return {
        id: n.id,
        position: { x: col * 190, y: row * 78 },
        data: { label: `${n.type.replace("_", " ")}\n${n.label}` },
        style: {
          width: 170,
          fontSize: 11,
          whiteSpace: "pre-line",
          border: `2px solid ${COLOR[n.type] ?? "#999"}`,
          borderRadius: 8,
          background: n.id === selectedClaimId ? "#fbe9f2" : "#fff",
          opacity: dim ? 0.25 : 1,
          padding: 6,
        },
      };
    });
    const edges: Edge[] = graph.edges.map((e) => {
      const highlighted = lineage !== null && lineage.has(e.source) && lineage.has(e.target);
      return {
        id: `${e.source}-${e.type}-${e.target}`,
        source: e.source,
        target: e.target,
        label: e.type,
        labelStyle: { fontSize: 9, fill: "#5b6574" },
        style: { ...(EDGE_STYLE[e.origin] ?? {}), strokeWidth: highlighted ? 2.2 : 1, opacity: lineage && !highlighted ? 0.2 : 1 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      };
    });
    return { nodes, edges };
  }, [graph, selectedClaimId]);

  return (
    <section className="panel graph">
      <header className="panel-header">
        <h2>Provenance graph</h2>
        <span className="hint">computed from records · click a claim</span>
      </header>
      <div className="graph-canvas">
        {graph ? (
          <ReactFlow
            // Remount when the graph grows or the run changes, so fitView frames all nodes.
            key={`${graph.run_id}:${graph.nodes.length}`}
            nodes={nodes}
            edges={edges}
            fitView
            nodesDraggable
            onNodeClick={(_, node) => {
              const type = graph.nodes.find((n) => n.id === node.id)?.type ?? "";
              onSelectNode(node.id, type);
            }}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        ) : (
          <p className="empty">The graph appears when a run starts.</p>
        )}
      </div>
      <div className="legend">
        <span><i className="edge-solid" /> backend-verified</span>
        <span><i className="edge-dashed" /> agent-proposed citation</span>
        <span><i className="edge-dotted" /> assumption (stated / method)</span>
      </div>
      {sequenceRange && (
        <div className="time-slider">
          <label htmlFor="seq">
            Timeline: event {atSequence ?? sequenceRange[1]} of {sequenceRange[1]}
          </label>
          <input
            id="seq"
            type="range"
            min={sequenceRange[0]}
            max={sequenceRange[1]}
            value={atSequence ?? sequenceRange[1]}
            aria-valuetext={`event ${atSequence ?? sequenceRange[1]} of ${sequenceRange[1]}`}
            onChange={(e) => {
              const value = Number(e.target.value);
              onSequence(value >= sequenceRange[1] ? null : value);
            }}
          />
          <span className="hint">graph as it existed at that moment (time order is not dependency)</span>
        </div>
      )}
    </section>
  );
}
