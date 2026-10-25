import { Background, Controls, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { FacetRef, RelationPage } from "../../api/storyOverview";
import { Button } from "../ui/Button";

export default function CharacterRelations({ data, selectedId, onSelect, onEvidence }: { data: RelationPage; selectedId: string; onSelect(ref: FacetRef): void; onEvidence(index: number): void }) {
  const names = new Map(data.nodes.map(node => [node.ref.facet_id, node.name]));
  const nodes = data.nodes.map((node, i) => ({ id: node.ref.facet_id, position: { x: i === 0 ? 240 : (i % 3) * 240, y: i === 0 ? 0 : 120 + Math.floor((i - 1) / 3) * 120 }, data: { label: node.name }, selected: node.ref.facet_id === selectedId }));
  const edges = data.items.map((edge, i) => ({ id: String(i), source: edge.left.facet_id, target: edge.right.facet_id,
    label: `${edge.label} · ${edge.nature === "declared" ? "设定" : edge.mode === "narration" ? "叙述" : edge.mode === "testimony" ? "证词" : edge.mode === "recollection" ? "回忆" : edge.mode === "hypothesis" ? "推测" : edge.mode === "dream" ? "梦" : "未知"}`,
    style: { stroke: edge.nature === "written" && edge.mode === "narration" ? "#557d68" : "#8195a5", strokeDasharray: edge.nature === "written" && edge.mode === "narration" ? undefined : "5 4" } }));
  return <section aria-label="人物关系">
    <h3>人物关系</h3>
    {edges.length > 0 ? <>
      <div className="overview-graph" aria-label="人物关系图"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, node) => { const facet = data.nodes.find(item => item.ref.facet_id === node.id); if (facet) onSelect(facet.ref); }}><Background /><Controls showInteractive={false} /></ReactFlow></div>
      <ul className="overview-relation-list">{data.items.map((edge, i) => <li key={i}>
        <button onClick={() => onSelect(edge.left)}>{names.get(edge.left.facet_id)}</button><span>{edge.label}</span><button onClick={() => onSelect(edge.right)}>{names.get(edge.right.facet_id)}</button>
        <span className="overview-muted">{edge.nature === "declared" ? "作者设定" : `第 ${edge.chapter} 章 · ${edge.mode === "narration" ? "叙述" : edge.mode === "testimony" ? "证词" : edge.mode === "recollection" ? "回忆" : edge.mode === "hypothesis" ? "推测" : edge.mode === "dream" ? "梦" : "未知"}`}</span><Button size="compact" variant="quiet" onClick={() => onEvidence(i)}>原文依据</Button>
      </li>)}</ul>
    </> : <p className="overview-muted">暂未发现有明确依据的人物关系。</p>}
  </section>;
}
