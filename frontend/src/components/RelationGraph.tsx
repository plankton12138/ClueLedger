/**
 * 角色关系图（P6 T10，计划 D8）：React Flow v12 + d3-force 初始布局。
 *
 * - 懒加载（BiblePanel 内 React.lazy）：首屏不拖累创作台；
 * - 数据：buildGraphData 纯函数（state/relations.ts）解析 relationships，
 *   指向已注册角色的条目成边；未连线条目图下原样列出（泄压阀）；
 * - 交互：点节点 → onSelect 联动小传（右侧展示由 BiblePanel 负责）；
 *   无任何可解析关系 → 空态引导（bible_upsert 补关系示例）。
 */

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forceCenter, forceLink, forceManyBody, forceSimulation } from "d3-force";
import { buildGraphData, type RelationInput } from "../state/relations";

interface CharNodeData extends Record<string, unknown> {
  label: string;
  active: boolean;
}

type CharFlowNode = Node<CharNodeData, "char">;

interface SimNode {
  id: string;
  x?: number;
  y?: number;
}

interface SimLink {
  source: string;
  target: string;
}

/** 自定义节点：左入/右出 Handles + 姓名徽章；选中态金边高亮。 */
function CharNode({ data }: NodeProps<CharFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <div
        className={`border px-2 py-1 text-xs transition-colors ${
          data.active
            ? "border-gold bg-gold/15 text-gold"
            : "border-line bg-ink text-paper hover:border-gold/50"
        }`}
      >
        {data.label}
      </div>
      <Handle type="source" position={Position.Right} />
    </>
  );
}

const nodeTypes = { char: CharNode };

/** d3-force 初始布局：同步 tick 300 次收敛，坐标交给 React Flow 后自由拖拽。 */
function layout(nodes: { id: string }[], edges: { source: string; target: string }[]) {
  const simNodes: SimNode[] = nodes.map((n) => ({ id: n.id }));
  if (simNodes.length > 0) {
    const links: SimLink[] = edges.map((e) => ({ source: e.source, target: e.target }));
    const sim = forceSimulation<SimNode>(simNodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance(90),
      )
      .force("charge", forceManyBody().strength(-220))
      .force("center", forceCenter(0, 0));
    for (let i = 0; i < 300; i += 1) sim.tick();
  }
  return simNodes;
}

interface RelationGraphProps {
  characters: Record<string, unknown>[];
  selected: string | null;
  onSelect: (name: string) => void;
}

export default function RelationGraph({ characters, selected, onSelect }: RelationGraphProps) {
  const { nodes, edges, unconnected, nodeCount } = useMemo(() => {
    const inputs: RelationInput[] = characters
      .map((c) => ({
        name: String(c["name"] ?? ""),
        relationships: Array.isArray(c["relationships"])
          ? (c["relationships"] as string[])
          : [],
      }))
      .filter((n) => n.name !== "");
    const graph = buildGraphData(inputs);
    const simNodes = layout(graph.nodes, graph.edges);
    const flowNodes: CharFlowNode[] = simNodes.map((n) => ({
      id: n.id,
      position: { x: n.x ?? 0, y: n.y ?? 0 },
      type: "char",
      data: { label: n.id, active: n.id === selected },
    }));
    const flowEdges: Edge[] = graph.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      label: e.label === "" ? undefined : e.label,
      labelStyle: { fill: "#8a8474", fontSize: 9 },
    }));
    return {
      nodes: flowNodes,
      edges: flowEdges,
      unconnected: graph.unconnected,
      nodeCount: graph.nodes.length,
    };
  }, [characters, selected]);

  if (nodeCount === 0) {
    return (
      <div className="grid h-full min-h-0 place-items-center px-4">
        <div className="max-w-sm text-center text-xs leading-relaxed text-paper/50">
          <p>关系图还没有可画的关系。</p>
          <p className="mt-2 text-gold-soft">让 agent 用 bible_upsert 补充角色关系：</p>
          <code className="mt-2 inline-block border border-line bg-ink px-2 py-1 text-[11px] text-gold">
            bible_upsert character 陆远 relationships:[&quot;林岚: 对手&quot;]
          </code>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, node) => onSelect(node.id)}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={18} />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      {unconnected.length > 0 && (
        <div className="max-h-28 flex-none overflow-auto border-t border-line px-3 py-2 text-[11px] leading-relaxed text-paper/60">
          <span className="text-gold-soft">未连线关系</span>
          <ul className="mt-1 space-y-0.5">
            {unconnected.map((u, i) => (
              <li key={i}>
                {u.source}：{u.entry}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
