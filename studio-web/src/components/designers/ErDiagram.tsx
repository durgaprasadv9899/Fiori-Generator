// Visual data model: entities as cards, compositions (solid) and associations (dashed).
import { memo, useEffect, useMemo } from "react";
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Icon } from "@ui5/webcomponents-react";
import "./designers.css";

const TYPE_ICON: Record<string, string> = {
  String: "text", LargeString: "document-text", Integer: "number-sign", Decimal: "lead", Date: "calendar",
  DateTime: "date-time", Boolean: "accept", UUID: "key",
};

type EntityData = { entity: any; root: boolean; selected: boolean; history?: boolean };

const EntityNode = memo(({ data }: NodeProps<Node<EntityData>>) => {
  const e = data.entity;
  const fields: any[] = e.fields || [];
  return (
    <div className={"ev-er" + (data.root ? " ev-er--root" : "") + (data.selected ? " is-sel" : "")}>
      <Handle type="target" position={Position.Left} className="ev-er__h" />
      <div className="ev-er__head">
        <Icon name={data.root ? "database" : "table-view"} />
        <div>
          <div className="ev-er__title">{e.label || e.name}</div>
          <div className="ev-er__set">{e.name}</div>
        </div>
        {data.root && <span className="ev-er__tag">root</span>}
      </div>
      <div className="ev-er__fields">
        <div className="ev-er__field ev-er__field--key"><Icon name="key" /><span>ID</span><em>UUID</em></div>
        {fields.slice(0, 12).map((f) => (
          <div key={f.name} className={"ev-er__field" + (f.formula ? " is-calc" : "")} title={f.name}>
            <Icon name={f.formula ? "simulate" : TYPE_ICON[f.type] || "text"} />
            <span>{f.label || f.name}{f.required ? <b className="ev-er__req">*</b> : null}</span>
            <em>{f.formula ? "calc" : f.enum?.length ? "enum" : f.type}</em>
          </div>
        ))}
        {fields.length > 12 && <div className="ev-er__more">+ {fields.length - 12} more</div>}
      </div>
      <Handle type="source" position={Position.Right} className="ev-er__h" />
    </div>
  );
});

const nodeTypes = { entity: EntityNode };

function layout(domain: any, selected: string): { nodes: Node<EntityData>[]; edges: Edge[] } {
  const ents: any[] = domain?.entities || [];
  const byName: Record<string, any> = Object.fromEntries(ents.map((e) => [e.name, e]));
  // depth = distance from the root through compositions / parent links
  const depth: Record<string, number> = {};
  const root = domain?.root || ents[0]?.name;
  const visit = (n: string, d: number) => {
    if (depth[n] !== undefined && depth[n] <= d) return;
    depth[n] = d;
    (byName[n]?.compositions || []).forEach((c: any) => byName[c.target] && visit(c.target, d + 1));
  };
  if (root) visit(root, 0);
  ents.forEach((e) => { if (depth[e.name] === undefined) depth[e.name] = e.parent && depth[e.parent] !== undefined ? depth[e.parent] + 1 : 0; });
  const cols: Record<number, string[]> = {};
  ents.forEach((e) => (cols[depth[e.name]] = [...(cols[depth[e.name]] || []), e.name]));
  const nodes: Node<EntityData>[] = [];
  Object.entries(cols).forEach(([d, names]) => {
    names.forEach((n, i) => {
      const h = 90 + Math.min(12, (byName[n].fields || []).length + 1) * 25;
      nodes.push({
        id: n, type: "entity", position: { x: Number(d) * 340, y: i * (h + 50) + (n === root ? 0 : 20) },
        data: { entity: byName[n], root: n === root, selected: n === selected },
      });
    });
  });
  const edges: Edge[] = [];
  ents.forEach((e) => {
    (e.compositions || []).forEach((c: any) => byName[c.target] && edges.push({
      id: `c-${e.name}-${c.name}`, source: e.name, target: c.target, label: `${c.label || c.name} (1:n)`, animated: true,
      markerEnd: { type: MarkerType.ArrowClosed }, className: "ev-edge ev-edge--comp",
    }));
    (e.associations || []).forEach((a: any) => byName[a.target] && edges.push({
      id: `a-${e.name}-${a.name}`, source: e.name, target: a.target, label: a.label || a.name,
      markerEnd: { type: MarkerType.Arrow }, style: { strokeDasharray: "6 4" }, className: "ev-edge ev-edge--assoc",
    }));
  });
  return { nodes, edges };
}

function Inner({ domain, selected, onSelect, height }: { domain: any; selected: string; onSelect: (n: string) => void; height: number }) {
  const init = useMemo(() => layout(domain, selected), [domain, selected]);
  const [nodes, setNodes, onNodesChange] = useNodesState(init.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(init.edges);
  useEffect(() => {
    setNodes((cur) => init.nodes.map((n) => ({ ...n, position: cur.find((c) => c.id === n.id)?.position || n.position })));
    setEdges(init.edges);
  }, [init, setNodes, setEdges]);
  return (
    <div className="ev-flow" style={{ height }}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeClick={(_, n) => onSelect(n.id)} fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.3} maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1} />
        <MiniMap pannable zoomable className="ev-flow__mini" nodeBorderRadius={10} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export function ErDiagram(props: { domain: any; selected: string; onSelect: (n: string) => void; height?: number }) {
  return <ReactFlowProvider><Inner {...props} height={props.height || 460} /></ReactFlowProvider>;
}
