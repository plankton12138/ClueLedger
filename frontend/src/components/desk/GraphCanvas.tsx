/**
 * 图谱画布（V03-6.4）：React Flow 承载节点/边，拖动只写本地布局偏好。
 *
 * 设计依据（产品 §5.4/§5.8、计划 M12）：
 * - 节点位置来自 `state/graphLayout.ts` 的确定性初始布局 + 本地偏好；拖动仅
 *   调用 `writeLayout`（localStorage），**不触发任何领域写入**——正式关系与手法
 *   的变更必须走账本编辑与确认路径。
 * - 侧栏窄栏不塞满所有节点：紧凑模式只画与当前工作相关的前若干个节点，其余
 *   交给「展开查看」。
 * - 画布不是唯一入口：节点/边同样可由文字目录（GraphPanel 的「文字目录」）选中，
 *   画布只是投影；节点可用 Tab 聚焦、回车选中，方向键移动选中节点（V03-6.5）——
 *   键盘移动与拖动写的是同一份布局偏好，不产生第二种事实。
 */

import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { GraphEdgeWire, GraphNodeWire } from "../../api/graphs";
import {
  readLayout,
  resolveLayout,
  settledPositions,
  writeLayout,
  type GraphLayout,
  type GraphViewId,
} from "../../state/graphLayout";
import { graphEmptyText } from "../../state/graphDirectory";

/** 紧凑模式最多显示的对象数：窄栏只给与当前工作相关的小图。 */
const COMPACT_NODE_LIMIT = 8;
/** 超过这个对象数只渲染视口内的节点；初始布局已按数量拉开，缩放能看全。 */
const VIRTUALIZE_NODE_LIMIT = 60;

const EVIDENCE_TONE: Record<string, string> = {
  valid: "bg-emerald-400",
  needs_check: "bg-amber-400",
  not_recorded: "bg-slate-300",
};

interface GraphNodeData extends Record<string, unknown> {
  label: string;
  kindLabel: string;
  evidence: string;
  active: boolean;
}

type GraphFlowNode = Node<GraphNodeData, "graph">;

/** 自定义节点：种类徽章 + 标题 + 证据状态点；选中态高亮边框。 */
function GraphNodeView({ data }: NodeProps<GraphFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <div
        className={`max-w-40 rounded-md border px-2 py-1 text-[11px] shadow-sm ${
          data.active
            ? "border-blue bg-blue-50 text-fgStrong"
            : "border-line bg-white text-fgStrong"
        }`}
      >
        <span className="mb-0.5 flex items-center gap-1 text-[9px] text-sub">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${EVIDENCE_TONE[data.evidence] ?? "bg-slate-300"}`}
            aria-hidden
          />
          {data.kindLabel}
        </span>
        <span className="block truncate">{data.label}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </>
  );
}

const nodeTypes = { graph: GraphNodeView };

export default function GraphCanvas({
  projectId,
  viewId,
  nodes: wireNodes,
  edges: wireEdges,
  selectedId,
  onSelectNode,
  onSelectEdge,
  compact = false,
}: {
  projectId: string;
  viewId: GraphViewId;
  nodes: GraphNodeWire[];
  edges: GraphEdgeWire[];
  selectedId: string | null;
  onSelectNode(id: string): void;
  onSelectEdge(id: string): void;
  compact?: boolean;
}) {
  // 紧凑模式：按 ID 取前若干个节点，边只保留两端都在其中者，避免画悬空线。
  const visible = useMemo(() => {
    if (!compact || wireNodes.length <= COMPACT_NODE_LIMIT) return wireNodes;
    const head = [...wireNodes].sort((a, b) => a.id.localeCompare(b.id)).slice(0, COMPACT_NODE_LIMIT);
    const keep = new Set(head.map((n) => n.id));
    if (selectedId && !keep.has(selectedId)) {
      const extra = wireNodes.find((n) => n.id === selectedId);
      if (extra) {
        head.push(extra);
        keep.add(selectedId);
      }
    }
    return head;
  }, [compact, wireNodes, selectedId]);
  const visibleIds = useMemo(() => new Set(visible.map((n) => n.id)), [visible]);
  const visibleEdges = useMemo(
    () => wireEdges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [wireEdges, visibleIds],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<GraphFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    const layout = resolveLayout(
      visible.map((n) => n.id),
      readLayout(projectId, viewId),
    );
    setNodes(
      visible.map((node) => ({
        id: node.id,
        type: "graph",
        position: layout[node.id] ?? { x: 0, y: 0 },
        data: {
          label: node.title || node.id,
          kindLabel: node.kind_label || node.kind,
          evidence: node.evidence_status,
          active: node.id === selectedId,
        },
      })),
    );
    setEdges(
      visibleEdges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label || undefined,
        labelStyle: { fill: "#6b7280", fontSize: 9 },
        style: edge.evidence_status === "needs_check" ? { stroke: "#f59e0b" } : undefined,
      })),
    );
  }, [
    projectId,
    viewId,
    visible,
    visibleEdges,
    selectedId,
    setNodes,
    setEdges,
  ]);

  /** 落定坐标写入布局偏好：拖动结束与键盘移动共用这一条落盘路径，不碰领域数据。 */
  function persistPositions(positions: GraphLayout) {
    writeLayout(projectId, viewId, { ...readLayout(projectId, viewId), ...positions });
  }

  /**
   * 位置变更先交给受控状态，再把**落定**坐标落盘（V03-6.5）：
   * 拖动中间帧只更新画面，拖动结束与键盘方向键移动都会写进本地偏好，
   * 避免「键盘挪了、刷新又弹回去」的两种事实。
   */
  function handleNodesChange(changes: Parameters<typeof onNodesChange>[0]) {
    onNodesChange(changes);
    const settled = settledPositions(changes);
    if (settled) persistPositions(settled);
  }

  if (wireNodes.length === 0) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center p-4 text-center text-xs leading-6 text-sub">
        {graphEmptyText(viewId)}
      </div>
    );
  }

  return (
    <div className={compact ? "h-40 min-h-0" : "h-full min-h-0"}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onEdgeClick={(_, edge) => onSelectEdge(edge.id)}
        onNodeDragStop={(_, node) => persistPositions({ [node.id]: { ...node.position } })}
        nodesConnectable={false}
        elementsSelectable
        nodesDraggable
        onlyRenderVisibleElements={wireNodes.length > VIRTUALIZE_NODE_LIMIT}
        minZoom={0.1}
        maxZoom={2}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        proOptions={{ hideAttribution: true }}
        aria-label="图谱画布（方向键移动选中节点、回车选择；拖动只调整本地布局）"
      >
        <Background gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
