import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BaseEdge, ControlButton, Controls, Handle, MarkerType, MiniMap, Position,
  ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type EdgeProps, type Node, type NodeProps, type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { StoryGraph, StoryNode, StoryStage } from "../../api/story";
import type { ELK } from "elkjs/lib/elk-api";
import CanvasScrollbars from "./CanvasScrollbars";

type CardNode = Node<{ story: StoryNode; choose: (id: string) => void }, "story">;
type StageNode = Node<{ stage: StoryStage }, "stage">;
type CanvasNode = CardNode | StageNode;
const views = new Map<string, Viewport>();
let engine: Promise<ELK> | undefined;
function layoutEngine() {
  return engine ??= import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());
}

function StoryCard({ data, selected }: NodeProps<CardNode>) {
  return <>
    <Handle type="target" position={Position.Top} />
    <button type="button" className="story-node" data-selected={selected || undefined}
      aria-label={data.story.title} aria-pressed={selected} onClick={() => data.choose(data.story.id)}>
      <span className="story-node-title">{data.story.title}</span>
      {data.story.action && <span className="story-node-summary">{data.story.action}</span>}
    </button>
    <Handle type="source" position={Position.Bottom} />
  </>;
}

function ResponseEdge(props: EdgeProps) {
  // 回应从后文指回前文，沿卡片左侧走线，不参与推进顺序的布局。
  const side = Math.min(props.sourceX, props.targetX) - 180;
  const path = `M ${props.sourceX} ${props.sourceY} L ${props.sourceX} ${props.sourceY + 18} L ${side} ${props.sourceY + 18} L ${side} ${props.targetY - 18} L ${props.targetX} ${props.targetY - 18} L ${props.targetX} ${props.targetY}`;
  return <BaseEdge {...props} path={path} />;
}

function StageBand({ data }: NodeProps<StageNode>) {
  return <div className="story-stage-band" aria-label={`阶段 ${data.stage.title}`}>
    <strong>{data.stage.title}</strong>
    {data.stage.summary && <span>{data.stage.summary}</span>}
  </div>;
}

const nodeTypes = { story: StoryCard, stage: StageBand };
const edgeTypes = { response: ResponseEdge };

function Canvas({ graph, nodeId, onSelect, viewKey }: {
  graph: StoryGraph; nodeId: string | null; onSelect: (id: string | null) => void; viewKey: string;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<string | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const started = useRef(false);
  const flow = useReactFlow<CanvasNode>();
  useEffect(() => {
    setNodes(previous => [
      ...graph.nodes.map(story => {
        const existing = previous.find(node => node.id === story.id);
        return { ...existing, id: story.id, type: "story" as const, position: existing?.position ?? { x: 0, y: 0 },
          data: { story, choose: (id: string) => onSelect(id) } } as CanvasNode;
      }),
      ...graph.stages.map(stage => {
        const id = `stage:${stage.id}`;
        const existing = previous.find(node => node.id === id);
        return { ...existing, id, type: "stage" as const, draggable: false, selectable: false, connectable: false,
          position: existing?.position ?? { x: 0, y: 0 }, data: { stage } } as CanvasNode;
      }),
    ]);
  }, [graph, onSelect, setNodes]);
  const sizes = nodes.filter(node => node.type === "story").map(node => `${node.id}:${node.measured?.width ?? 280}:${node.measured?.height ?? 112}`).join("|");
  useEffect(() => {
    if (nodes.filter(node => node.type === "story").length !== graph.nodes.length || !graph.nodes.length) return;
    let cancelled = false;
    const dimensions = nodes.filter(node => node.type === "story").map(node => ({ id: node.id, width: node.measured?.width ?? 280, height: node.measured?.height ?? 112 }));
    void layoutEngine().then(elk => elk.layout({
      id: "story", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "DOWN",
        "elk.spacing.nodeNode": "48", "elk.layered.spacing.nodeNodeBetweenLayers": "64",
        "elk.padding": "[top=28,left=200,bottom=28,right=40]" },
      children: dimensions,
      edges: graph.edges.filter(edge => edge.kind === "next").map(edge => ({ id: edge.id, sources: [edge.from_id], targets: [edge.to_id] })),
    })).then(result => {
      if (cancelled) return;
      setError(null);
      const positions = new Map(result.children?.map(node => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]));
      setNodes(previous => previous.map(node => {
        if (node.type !== "stage") return { ...node, position: positions.get(node.id) ?? node.position };
        const stageId = node.id.slice("stage:".length);
        const members = graph.nodes.filter(item => item.stage_id === stageId).map(item => positions.get(item.id)).filter(Boolean) as { x: number; y: number }[];
        if (members.length === 0) return node;
        const x = Math.min(...members.map(item => item.x)) - 12;
        const y = Math.min(...members.map(item => item.y)) - 52;
        return { ...node, position: { x, y } };
      }));
      if (!started.current) {
        const first = result.children?.slice().sort((left, right) => (left.y ?? 0) - (right.y ?? 0))[0];
        const width = host.current?.clientWidth ?? 800;
        void flow.setViewport(views.get(viewKey) ?? { x: width / 2 - (first?.x ?? 0) - (first?.width ?? 280) / 2, y: 0, zoom: 1 });
        started.current = true;
      }
    }).catch(() => { if (!cancelled) setError("[STORY_LAYOUT_FAILED] 构思画布：自动排布未完成。建议：重新打开构思。"); });
    return () => { cancelled = true; };
    // 尺寸决定排布；选中、拖动或缩放不触发重排。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, sizes, flow, setNodes, viewKey]);
  useEffect(() => {
    if (!nodeId) return;
    const target = flow.getNode(nodeId);
    if (!target) return;
    const width = target.measured?.width ?? 280;
    const height = target.measured?.height ?? 112;
    void flow.setCenter(target.position.x + width / 2, target.position.y + height / 2, {
      zoom: Math.max(0.8, Math.min(1.4, flow.getZoom())), duration: 240,
    });
  }, [nodeId, flow, nodes]);
  const edges = useMemo(() => graph.edges.map(edge => {
    const related = edge.from_id === nodeId || edge.to_id === nodeId;
    return { id: edge.id, source: edge.from_id, target: edge.to_id,
      type: edge.kind === "next" ? "smoothstep" : "response",
      className: `story-flow-edge${related ? " is-related" : ""}${edge.kind !== "next" ? " is-response" : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed },
      hidden: edge.kind !== "next" && !related,
      ariaLabel: edge.reason || (edge.kind === "next" ? "推进" : "后文回应"),
    };
  }), [graph, nodeId]);
  return <div className="story-canvas" ref={host} role="group" aria-label="结构图">
    {error && <p role="alert" className="story-canvas-message">{error}</p>}
    {connection && <button type="button" className="story-connection" onClick={() => setConnection(null)}>{connection} · 关闭</button>}
    <ReactFlow<CanvasNode> nodes={nodes.map(node => ({ ...node, selected: node.type === "story" && node.id === nodeId }))} edges={edges}
      nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange}
      nodesConnectable={false} elementsSelectable={false} deleteKeyCode={null}
      onPaneClick={() => { onSelect(null); setConnection(null); }}
      onEdgeClick={(_, edge) => setConnection(graph.edges.find(item => item.id === edge.id)?.reason || edge.ariaLabel || "")}
      onViewportChange={viewport => views.set(viewKey, viewport)}
      minZoom={0.001} maxZoom={4} panOnScroll zoomOnScroll={false} zoomOnPinch
      style={{ width: "calc(100% - 18px)", height: "calc(100% - 18px)" }}
      ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "放大画布", "controls.zoomOut.ariaLabel": "缩小画布", "controls.fitView.ariaLabel": "查看全貌" }}
      aria-label="纵向构思画布">
      <Background gap={20} size={1} />
      <Controls showInteractive={false} fitViewOptions={{ minZoom: 0.001, maxZoom: 1, padding: 0.15 }} aria-label="画布缩放与全貌">
        <ControlButton onClick={() => void flow.zoomTo(1)} title="恢复100%缩放" aria-label="恢复100%缩放">1:1</ControlButton>
      </Controls>
      {graph.nodes.length > 15 && <MiniMap pannable zoomable ariaLabel="构思概览" />}
    </ReactFlow>
    <CanvasScrollbars nodes={nodes} />
  </div>;
}

export default function StoryCanvas(props: Parameters<typeof Canvas>[0]) {
  return <ReactFlowProvider key={props.viewKey}><Canvas {...props} /></ReactFlowProvider>;
}
