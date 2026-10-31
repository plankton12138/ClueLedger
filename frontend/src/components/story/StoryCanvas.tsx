import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BaseEdge, ControlButton, Controls, Handle, MarkerType, MiniMap, Position,
  ReactFlow, ReactFlowProvider, useNodesState, useReactFlow, useViewport,
  type EdgeProps, type Node, type NodeProps, type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { StoryGraph, StoryNode, StoryStage, StoryThread } from "../../api/story";
import { layoutStory, stageKey, threadKey, cardWidth, columnWidth } from "./storyLayout";
import CanvasScrollbars from "./CanvasScrollbars";

type CardNode = Node<{ story: StoryNode; choose: (id: string) => void }, "story">;
type StageNode = Node<{ stage: StoryStage }, "stage">;
type ThreadData = { thread: StoryThread; expanded: boolean; toggle: (open: boolean) => void };
type ThreadNode = Node<ThreadData, "thread">;
type CanvasNode = CardNode | StageNode | ThreadNode;
const views = new Map<string, Viewport>();
function StoryCard({ data, selected }: NodeProps<CardNode>) {
  return <>
    <Handle type="target" position={Position.Top} />
    <button type="button" className="story-node" data-selected={selected || undefined}
      aria-label={data.story.title} aria-pressed={selected} onClick={() => data.choose(data.story.id)}>
      <span className="story-node-title">{data.story.title}</span>
      {(data.story.change || data.story.action) && <span className="story-node-summary">{data.story.change || data.story.action}</span>}
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
  return <><Handle type="target" position={Position.Top} /><div className="story-stage-band" title={data.stage.summary ?? undefined} aria-label={`阶段 ${data.stage.title}`}>
    <strong>{data.stage.title}</strong>
  </div><Handle type="source" position={Position.Bottom} /></>;
}

function ThreadHeading({ data }: NodeProps<ThreadNode>) {
  // 图内只保留测量占位；实际可见表头由冻结层绘制，避免重复标题和焦点。
  return <div className="story-thread-heading-measure" aria-hidden="true" style={{ visibility: "hidden" }}>
    <HeadingContent data={data} />
  </div>;
}

function HeadingContent({ data, interactive = false }: { data: ThreadData; interactive?: boolean }) {
  return <><strong>{data.thread.title}</strong>
    {(data.thread.goal || data.thread.obstacle) && <details open={data.expanded}
      onToggle={interactive ? event => data.toggle(event.currentTarget.open) : undefined}>
      <summary>目标与阻力</summary><p>{data.thread.goal}</p><p>{data.thread.obstacle}</p>
    </details>}
  </>;
}

function FrozenThreadHeadings({ headings }: { headings: ThreadNode[] }) {
  const { x, zoom } = useViewport();
  if (!headings.length) return null;
  const height = Math.max(...headings.map(n => n.measured?.height ?? 64));
  return <div className="story-frozen-headings" role="region" aria-label="故事线表头"
    style={{ height: 16 + height * zoom }}>
    <div style={{ transform: `translate(${x}px, 8px) scale(${zoom})`, transformOrigin: "0 0" }}>
      {headings.map(node => <div key={node.id} className="story-thread-heading"
        data-thread-id={node.data.thread.id}
        style={{ position: "absolute", left: node.position.x, top: 0, width: node.style?.width }}>
        <HeadingContent data={node.data} interactive />
      </div>)}
    </div>
  </div>;
}

const nodeTypes = { story: StoryCard, stage: StageBand, thread: ThreadHeading };
const edgeTypes = { response: ResponseEdge };

function Canvas({ graph, nodeId, onSelect, viewKey }: {
  graph: StoryGraph; nodeId: string | null; onSelect: (id: string | null) => void; viewKey: string;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<string | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const started = useRef(false);
  const [available, setAvailable] = useState(1014);
  const [expandedThreads, setExpandedThreads] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver(entries => setAvailable(Math.max(240, entries[0].contentRect.width - 18)));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const flow = useReactFlow<CanvasNode>();
  useEffect(() => {
    setNodes(previous => [
      ...graph.nodes.map(story => {
        const existing = previous.find(node => node.id === story.id);
        return { ...existing, id: story.id, type: "story" as const, draggable: false, className: "nopan", position: existing?.position ?? { x: 0, y: 0 },
          style: { width: cardWidth(graph, story, available), pointerEvents: "all" }, data: { story, choose: (id: string) => onSelect(id) } } as CanvasNode;
      }),
      ...graph.stages.map(stage => {
        const id = stageKey(stage.id);
        const existing = previous.find(node => node.id === id);
        return { ...existing, id, type: "stage" as const, draggable: false, selectable: false, connectable: false,
          position: existing?.position ?? { x: 0, y: 0 }, style: { width: 88 }, data: { stage } } as CanvasNode;
      }),
      ...graph.threads.map(thread => {
        const id = threadKey(thread.id);
        const existing = previous.find(node => node.id === id);
        return { ...existing, id, type: "thread" as const, draggable: false, selectable: false, connectable: false, focusable: false,
          position: existing?.position ?? { x: 0, y: 0 }, style: { width: columnWidth(graph, available), pointerEvents: "none" },
          data: { thread, expanded: expandedThreads.has(thread.id), toggle: (open: boolean) => setExpandedThreads(previous => {
            const next = new Set(previous); if (open) next.add(thread.id); else next.delete(thread.id); return next;
          }) } } as CanvasNode;
      }),
    ]);
  }, [graph, onSelect, setNodes, available, expandedThreads]);
  const sizes = nodes.map(node => `${node.id}:${node.measured?.width ?? 280}:${node.measured?.height ?? 112}`).join("|");
  const [layoutReady, setLayoutReady] = useState<StoryGraph | null>(null);
  useEffect(() => {
    if (nodes.length !== graph.nodes.length + graph.stages.length + graph.threads.length) return;
    try {
      const positions = layoutStory(graph, new Map(nodes.map(n => [n.id, { width: n.measured?.width ?? 280, height: n.measured?.height ?? 112 }])), available);
      setNodes(previous => previous.map(n => ({ ...n, position: positions.get(n.id) ?? n.position })));
      setError(null);
      setLayoutReady(graph);
      if (!started.current) {
        void flow.setViewport(views.get(viewKey) ?? { x: 8, y: 0, zoom: 1 });
        started.current = true;
      }
    } catch {
      setError("[STORY_LAYOUT_FAILED] 构思画布：自动排布未完成。建议：检查推进关系后重新打开构思。");
    }
    // 实际测量包含阶段和列头；选择和视口不触发重排。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, sizes, flow, setNodes, viewKey, available]);
  useEffect(() => {
    if (!nodeId || layoutReady !== graph) return;
    const target = flow.getNode(nodeId);
    if (!target) return;
    const width = target.measured?.width ?? 280;
    const height = target.measured?.height ?? 112;
    void flow.setCenter(target.position.x + width / 2, target.position.y + height / 2, {
      zoom: Math.max(0.8, Math.min(1.4, flow.getZoom())), duration: 240,
    });
  }, [nodeId, flow, nodes, layoutReady, graph]);
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
      nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} deleteKeyCode={null}
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
      {graph.nodes.length > 15 && <MiniMap pannable zoomable style={{ width: 110, height: 72 }} ariaLabel="构思概览" />}
    </ReactFlow>
    <FrozenThreadHeadings headings={nodes.filter((n): n is ThreadNode => n.type === "thread")} />
    <CanvasScrollbars nodes={nodes} />
  </div>;
}

export default function StoryCanvas(props: Parameters<typeof Canvas>[0]) {
  return <ReactFlowProvider key={props.viewKey}><Canvas {...props} /></ReactFlowProvider>;
}
