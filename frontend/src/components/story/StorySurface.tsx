/**
 * 构思面（1.0.1 P5）。
 *
 * 显示当前构思结构与节点安排。图只画关系，不画示意图——节点位置由
 * 推进关系分层得出，连线就是真实存在的 `next / reframe / answer`。没有节点的作品
 * 不显示假结构，只说明可以怎么开始。
 */

import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus, Undo2, X } from "lucide-react";

import { collapseStages } from "./storyLayout";
import StoryDraft from "./StoryDraft";
import StoryBatches from "./StoryBatches";
import StoryCreationDialog, { type StoryCreationTarget } from "./StoryCreationDialog";
import { useConversationsStore } from "../../state/conversations";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
const StoryCanvas = lazy(() => import("./StoryCanvas"));

export interface StoryBindingLocator {
  variantId: string;
  path: string;
  start: number;
  end: number;
  quote: string;
}

export default function StorySurface({
  workspaceId,
  onStart,
  onReference,
  onLocate,
}: {
  workspaceId: string;
  /** 创建请求受理后打开对话，显示实际任务进度。 */
  onStart: () => void;
  /** 把选中的节点带进对话：作者不用再复述结构，下一轮请求会带上它。 */
  onReference: () => void;
  /** 在正文里打开这条关联的区间；返回由来源提示负责。 */
  onLocate: (binding: StoryBindingLocator) => void;
}) {
  const state = useStoryStore();
  const load = useStoryStore((store) => store.load);
  const selectNode = useStoryStore((store) => store.selectNode);
  const undo = useStoryStore((store) => store.undo);
  const referenceNode = useStoryStore((store) => store.referenceNode);
  const [stageId, setStageId] = useState<string | null>(null);
  const [detailHeight, setDetailHeight] = useState(260);
  const detailDrag = useRef<{ y: number; height: number } | null>(null);
  const [creationTarget, setCreationTarget] = useState<StoryCreationTarget | null>(null);

  useEffect(() => {
    setCreationTarget(null);
    setStageId(null);
    void load(workspaceId);
  }, [load, workspaceId]);

  const graph = state.graph;
  const stages = graph?.stages ?? [];
  const activeStage = stages.find((stage) => stage.id === stageId) ?? null;
  const visibleGraph = useMemo(() => {
    if (!graph || !activeStage) return graph;
    return collapseStages(graph, activeStage.id);
  }, [graph, activeStage]);
  useEffect(() => {
    if (!graph || !state.nodeId) return;
    const node = graph.nodes.find((item) => item.id === state.nodeId);
    if (node?.stage_id && activeStage && node.stage_id !== activeStage.id) setStageId(node.stage_id);
  }, [graph, state.nodeId, activeStage]);
  const nodesById = useMemo(
    () => new Map((graph?.nodes ?? []).map((node) => [node.id, node])),
    [graph],
  );

  if (state.loading && !graph) {
    return <div className="story-surface story-surface-empty">正在读取构思…</div>;
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="story-surface story-surface-empty">
        <StoryDraft key={workspaceId} workspaceId={workspaceId} />
        {state.error ? (
          <StatusMessage tone="error" action={<Button onClick={() => void load(workspaceId)}>重新读取</Button>}>
            {state.error}
          </StatusMessage>
        ) : (
          <EmptyState action={<Button variant="primary" onClick={() => setCreationTarget({
            workspaceId,
            conversationId: useConversationsStore.getState().selectedId,
            variantId: state.variantId ?? "",
            revision: graph?.revision ?? "",
            name: "构思",
          })}>创建构思</Button>}>
            还没有构思。根据创作要求或已有对话，建立节点、线索与连接。
          </EmptyState>
        )}
        {creationTarget && <StoryCreationDialog target={creationTarget}
          onClose={() => setCreationTarget(null)}
          onAccepted={() => { setCreationTarget(null); onStart(); }} />}
      </div>
    );
  }

  const detail = state.detail;
  return (
    <div className="story-surface">
      {state.lastChange && <header className="story-toolbar">
        <div className="story-actions">
          {state.lastChange && (
            <Button size="compact" disabled={state.busy} onClick={() => void undo()}>
              <Undo2 size={16} strokeWidth={2} aria-hidden="true" />撤销上一次
            </Button>
          )}
        </div>
      </header>}

      <StoryDraft key={workspaceId} workspaceId={workspaceId} />
      <StoryBatches key={workspaceId} workspaceId={workspaceId} />
      {stages.length > 0 && (
        <div className="story-groups" role="group" aria-label="阶段">
          <button type="button" className="ui-button" aria-pressed={!activeStage} onClick={() => setStageId(null)}>全部</button>
          {stages.map((stage) => <button
            key={stage.id}
            type="button"
            className="ui-button"
            aria-pressed={activeStage?.id === stage.id}
            onClick={() => setStageId(activeStage?.id === stage.id ? null : stage.id)}
          >{stage.title}</button>)}
          {activeStage && <span className="story-hint">{activeStage.summary || "尚未写阶段摘要"}</span>}
          {activeStage && (visibleGraph?.collapsed.length ?? 0) > 0 && <span className="story-hint">{visibleGraph?.collapsed.length} 个节点在其他阶段</span>}
        </div>
      )}

      {state.error && (
        <p className="story-error" role="alert">
          {state.error}
          <button type="button" className="ui-button" onClick={() => state.dismissError()}>
            知道了
          </button>
        </p>
      )}

      <div className="story-body">
        <Suspense fallback={<div className="story-canvas">正在打开构思画布…</div>}><StoryCanvas graph={visibleGraph ?? graph} nodeId={state.nodeId} onSelect={selectNode}
          viewKey={`${workspaceId}:${graph.variant_id}:${stageId ?? "all"}`} /></Suspense>

        {detail && <div role="separator" aria-orientation="horizontal" aria-label="节点详情高度"
          aria-valuemin={140} aria-valuemax={480} aria-valuenow={detailHeight} tabIndex={0}
          className="story-detail-resize"
          onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); detailDrag.current = { y: event.clientY, height: detailHeight }; }}
          onPointerMove={event => { if (detailDrag.current) setDetailHeight(Math.max(140, Math.min(480, detailDrag.current.height + detailDrag.current.y - event.clientY))); }}
          onPointerUp={() => { detailDrag.current = null; }} onPointerCancel={() => { detailDrag.current = null; }}
          onKeyDown={event => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); setDetailHeight(height => Math.max(140, Math.min(480, height + (event.key === "ArrowUp" ? 20 : -20)))); } }} />}
        <aside className="story-detail" aria-label="节点详情" hidden={!detail} style={{ flexBasis: detailHeight }}>
          {detail && (
            <>
              <div className="story-detail-heading"><h2>{detail.node.title}</h2><Button size="compact" onClick={() => void selectNode(null)} aria-label="收起节点详情"><X size={16} aria-hidden="true" /></Button></div>
              <p className="story-action">{detail.node.action}</p>
              {detail.node.change && <p className="story-hint">变化：{detail.node.change}</p>}
              {detail.node.stage_id && <p className="story-hint">阶段：{graph.stages.find(stage => stage.id === detail.node.stage_id)?.title ?? detail.node.stage_id}</p>}
              {detail.node.thread_ids.length > 0 && (
                <p className="story-hint">
                  分支：
                  {detail.node.thread_ids
                    .map((id) => graph.threads.find((item) => item.id === id)?.title ?? id)
                    .join("、")}
                </p>
              )}
              {detail.clue_uses.length > 0 && (
                <section>
                  {detail.clue_uses.map((use) => (
                    <article key={use.clue_id} className="story-clue">
                      <h3>{use.title}</h3>
                      <p>{use.presentation}</p>
                      {(use.author_truth || use.expected_interpretation) && <details className="story-truth-details"><summary>真实作用与后续回应</summary>
                      {use.author_truth && (
                        <p className="story-truth">真相：{use.author_truth}</p>
                      )}
                      {use.expected_interpretation && (
                        <p className="story-hint">想引导：{use.expected_interpretation}</p>
                      )}
                      </details>}
                    </article>
                  ))}
                </section>
              )}
              {detail.knowledge_changes.length > 0 && (
                <section>
                  <h3>知情</h3>
                  {detail.knowledge_changes.map((change, index) => (
                    <p key={index} className="story-hint">
                      {String(change.subject_id)}：
                      {String(change.stance) === "knows" ? "知道" : "相信"}
                      {String(change.claim)}
                    </p>
                  ))}
                </section>
              )}
              {detail.bindings.length > 0 && (
                <section>
                  <h3>正文</h3>
                  {detail.bindings.map((binding) => (
                    <button
                      key={binding.id}
                      type="button"
                      className="story-binding"
                      title={`${binding.path} · ${binding.start}–${binding.end}`}
                      onClick={() =>
                        onLocate({
                          variantId: state.variantId ?? state.primaryId,
                          path: binding.path,
                          start: binding.start,
                          end: binding.end,
                          quote: binding.quote,
                        })
                      }
                    >
                      {binding.quote}
                    </button>
                  ))}
                  <p className="story-hint">打开正文时会定位到这段；对不上就只提示需要重新确认。</p>
                </section>
              )}
              <div className="story-detail-actions">
                <Button
                  size="compact"
                  onClick={() => {
                    referenceNode(detail.node.id);
                    onReference();
                  }}
                >
                  <MessageSquarePlus size={16} strokeWidth={2} aria-hidden="true" />引用到对话
                </Button>
              </div>
              {detail.outgoing.length + detail.incoming.length > 0 && (
                <nav className="story-node-links" aria-label="节点连接">
                  {detail.incoming.map(edge => <button key={edge.id} type="button" className="ui-button"
                    aria-label={`定位到${nodesById.get(edge.from_id)?.title ?? edge.from_id}`}
                    onClick={() => void selectNode(edge.from_id)}>← {nodesById.get(edge.from_id)?.title ?? edge.from_id}</button>)}
                  {detail.outgoing.map(edge => <button key={edge.id} type="button" className="ui-button"
                    aria-label={`定位到${nodesById.get(edge.to_id)?.title ?? edge.to_id}`}
                    onClick={() => void selectNode(edge.to_id)}>{nodesById.get(edge.to_id)?.title ?? edge.to_id} →</button>)}
                </nav>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
