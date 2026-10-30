/**
 * 构思面（1.0.1 P5）。
 *
 * 显示当前构思结构与节点安排。图只画关系，不画示意图——节点位置由
 * 推进关系分层得出，连线就是真实存在的 `next / reframe / answer`。没有节点的作品
 * 不显示假结构，只说明可以怎么开始。
 */

import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus, Undo2, X } from "lucide-react";

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
  const fold = useStoryStore((store) => store.fold);
  const referenceNode = useStoryStore((store) => store.referenceNode);
  const [detailHeight, setDetailHeight] = useState(260);
  const detailDrag = useRef<{ y: number; height: number } | null>(null);
  const [creationTarget, setCreationTarget] = useState<StoryCreationTarget | null>(null);

  useEffect(() => {
    setCreationTarget(null);
    void load(workspaceId);
  }, [load, workspaceId]);

  const graph = state.graph;
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

      <StoryBatches key={workspaceId} workspaceId={workspaceId} />
      {state.groups.length > 0 && (
        <div className="story-groups" role="group" aria-label="片段">
          {state.groups.length > 8 ? <select
            className="ui-input"
            aria-label="选择片段"
            value={state.groupId ?? ""}
            onChange={(event) => void fold(event.target.value || null)}
          >
            <option value="">全部</option>
            {state.groups.map((group) => <option key={group} value={group}>{group}</option>)}
          </select> : <><button
            type="button"
            className="ui-button"
            aria-pressed={!state.groupId}
            onClick={() => void fold(null)}
          >
            全部
          </button>
          {state.groups.map((group) => (
            <button
              key={group}
              type="button"
              className="ui-button"
              aria-pressed={state.groupId === group}
              onClick={() => void fold(group)}
            >
              {group}
            </button>
          ))}</>}
          {state.groupId && graph.collapsed.length > 0 && (
            <span className="story-hint">{graph.collapsed.length} 个节点不在这个片段里</span>
          )}
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
        <Suspense fallback={<div className="story-canvas">正在打开构思画布…</div>}><StoryCanvas graph={graph} nodeId={state.nodeId} onSelect={selectNode}
          viewKey={`${workspaceId}:${graph.variant_id}:${state.groupId ?? "all"}`} /></Suspense>

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
              {detail.node.threads.length > 0 && (
                <p className="story-hint">
                  分支：
                  {detail.node.threads
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
                <p className="story-hint">
                  连接：
                  {detail.incoming
                    .map((edge) => `${nodesById.get(edge.from_id)?.title ?? edge.from_id} →`)
                    .join(" ")}
                  {detail.outgoing
                    .map((edge) => `→ ${nodesById.get(edge.to_id)?.title ?? edge.to_id}`)
                    .join(" ")}
                </p>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
