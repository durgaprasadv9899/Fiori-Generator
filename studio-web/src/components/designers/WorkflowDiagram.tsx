// Business process as a state diagram: states coloured by criticality, transitions as labelled arrows.
import { memo, useMemo } from "react";
import {
  Background, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./designers.css";

const TONE: Record<string, string> = { Positive: "good", Negative: "bad", Critical: "warn", Neutral: "neutral", Information: "info" };

type StateData = { label: string; code: string; tone: string; initial: boolean; editable: boolean };
const StateNode = memo(({ data }: NodeProps<Node<StateData>>) => (
  <div className={`ev-state ev-state--${data.tone}`}>
    <Handle type="target" position={Position.Left} id="l" className="ev-er__h" />
    <Handle type="target" position={Position.Top} id="t" className="ev-er__h" />
    <div className="ev-state__label">{data.label}</div>
    <div className="ev-state__code">{data.code}{data.initial ? " · start" : ""}{data.editable ? " · editable" : ""}</div>
    <Handle type="source" position={Position.Right} id="r" className="ev-er__h" />
    <Handle type="source" position={Position.Bottom} id="b" className="ev-er__h" />
  </div>
));
const nodeTypes = { state: StateNode };

export function WorkflowDiagram({ workflow, height = 300 }: { workflow: any; height?: number }) {
  const { nodes, edges } = useMemo(() => {
    const states: any[] = workflow?.states || [];
    const trans: any[] = workflow?.transitions || [];
    // rank = distance from the initial state (BFS over transitions); states of equal rank stack vertically,
    // positive outcomes above negative ones
    const rank: Record<string, number> = {};
    const start = workflow?.initial || states[0]?.code;
    if (start) {
      rank[start] = 0;
      const queue = [start];
      while (queue.length) {
        const cur = queue.shift()!;
        trans.filter((t) => (t.from || []).includes(cur)).forEach((t) => {
          if (rank[t.to] === undefined) { rank[t.to] = rank[cur] + 1; queue.push(t.to); }
        });
      }
    }
    states.forEach((s, i) => { if (rank[s.code] === undefined) rank[s.code] = i; });
    const order = (s: any) => (TONE[s.criticality] === "bad" ? 2 : TONE[s.criticality] === "good" ? 0 : 1);
    const cols: Record<number, any[]> = {};
    states.forEach((s) => (cols[rank[s.code]] = [...(cols[rank[s.code]] || []), s]));
    Object.values(cols).forEach((c) => c.sort((a, b) => order(a) - order(b)));
    const idx = rank;
    const nodes: Node<StateData>[] = [];
    Object.entries(cols).forEach(([r, col]) => col.forEach((s, j) => nodes.push({
      id: s.code, type: "state", draggable: true,
      position: { x: Number(r) * 270, y: j * 130 - (col.length - 1) * 65 },
      data: { label: s.label || s.code, code: s.code, tone: TONE[s.criticality] || "neutral",
        initial: s.code === workflow.initial, editable: (workflow.editable_states || []).includes(s.code) },
    })));
    const edges: Edge[] = [];
    trans.forEach((t: any) => (t.from || []).forEach((f: string) => {
      if (idx[f] === undefined || idx[t.to] === undefined) return;
      const back = idx[t.to] <= idx[f];
      edges.push({
        id: `${t.action}-${f}`, source: f, target: t.to, sourceHandle: back ? "b" : "r", targetHandle: back ? "t" : "l",
        label: t.label + (t.role && workflow.enforce_roles ? ` · ${t.role}` : ""), type: "smoothstep", animated: !back,
        markerEnd: { type: MarkerType.ArrowClosed }, className: back ? "ev-edge ev-edge--back" : "ev-edge ev-edge--fwd",
      });
    }));
    return { nodes, edges };
  }, [workflow]);
  return (
    <ReactFlowProvider>
      <div className="ev-flow" style={{ height }}>
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.25 }}
          nodesConnectable={false} proOptions={{ hideAttribution: true }} minZoom={0.4} maxZoom={1.5}>
          <Background gap={18} size={1} />
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}
