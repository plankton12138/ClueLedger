/**
 * 构思面（1.0.1 P5）。
 *
 * 一屏看三件事：方案、结构、当前节点的安排。图只画关系，不画示意图——节点位置由
 * 推进关系分层得出，连线就是真实存在的 `next / reframe / answer`。没有节点的作品
 * 不显示假结构，只说明可以怎么开始。
 */

import { useEffect, useMemo, useState } from "react";
import { CheckCheck, GitFork, MessageSquarePlus, Undo2 } from "lucide-react";

import ReaderPreview from "./ReaderPreview";
import StoryBatches from "./StoryBatches";
import StoryCandidates from "./StoryCandidates";
import StoryCreationDialog, { type StoryCreationTarget } from "./StoryCreationDialog";
import { useConversationsStore } from "../../state/conversations";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import type { StoryEdge, StoryGraph } from "../../api/story";

export interface StoryBindingLocator {
  variantId: string;
  path: string;
  start: number;
  end: number;
  quote: string;
}

const NODE_WIDTH = 168;
const NODE_HEIGHT = 64;
const GAP_X = 56;
const GAP_Y = 24;

interface Positioned {
  id: string;
  x: number;
  y: number;
  layer: number;
}

/** 按 `next` 关系分层；没有前驱的节点在第 0 层，孤立节点同样从第 0 层开始。 */
function layout(graph: StoryGraph): { positions: Map<string, Positioned>; width: number; height: number } {
  const incoming = new Map<string, number>();
  for (const node of graph.nodes) incoming.set(node.id, 0);
  for (const edge of graph.edges) {
    if (edge.kind === "next") incoming.set(edge.to_id, (incoming.get(edge.to_id) ?? 0) + 1);
  }
  const layer = new Map<string, number>();
  const queue = graph.nodes.filter((node) => (incoming.get(node.id) ?? 0) === 0).map((node) => node.id);
  for (const id of queue) layer.set(id, 0);
  let guard = graph.nodes.length * graph.nodes.length + 8;
  while (queue.length && guard-- > 0) {
    const id = queue.shift() as string;
    for (const edge of graph.edges) {
      if (edge.kind !== "next" || edge.from_id !== id) continue;
      const next = Math.max(layer.get(edge.to_id) ?? 0, (layer.get(id) ?? 0) + 1);
      if (next > (layer.get(edge.to_id) ?? 0)) {
        layer.set(edge.to_id, next);
        queue.push(edge.to_id);
      }
    }
  }
  const columns = new Map<number, string[]>();
  const positions = new Map<string, Positioned>();
  for (const node of graph.nodes) {
    const column = layer.get(node.id) ?? 0;
    const bucket = columns.get(column) ?? [];
    bucket.push(node.id);
    columns.set(column, bucket);
  }
  let height = NODE_HEIGHT;
  for (const [column, ids] of [...columns.entries()].sort((left, right) => left[0] - right[0])) {
    ids.sort();
    ids.forEach((id, index) => {
      positions.set(id, {
        id,
        layer: column,
        x: column * (NODE_WIDTH + GAP_X),
        y: index * (NODE_HEIGHT + GAP_Y),
      });
    });
    height = Math.max(height, ids.length * (NODE_HEIGHT + GAP_Y) - GAP_Y);
  }
  const width = Math.max(1, [...columns.keys()].length) * (NODE_WIDTH + GAP_X) - GAP_X;
  return { positions, width: Math.max(width, NODE_WIDTH), height };
}

function edgePath(edge: StoryEdge, from: Positioned, to: Positioned): string {
  const x1 = from.x + NODE_WIDTH;
  const y1 = from.y + NODE_HEIGHT / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_HEIGHT / 2;
  if (edge.kind === "next") {
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
  }
  // 重新解释与回收：从后文指回前文，用虚线区分，不与推进关系混在一起。
  const lift = Math.max(40, Math.abs(x2 - x1) / 3);
  return `M ${x1} ${y1} C ${x1 + lift} ${y1 - lift}, ${x2 - lift} ${y2 - lift}, ${x2} ${y2}`;
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
  const selectVariant = useStoryStore((store) => store.selectVariant);
  const selectNode = useStoryStore((store) => store.selectNode);
  const undo = useStoryStore((store) => store.undo);
  const fork = useStoryStore((store) => store.fork);
  const compare = useStoryStore((store) => store.compareWithPrimary);
  const fold = useStoryStore((store) => store.fold);
  const adopt = useStoryStore((store) => store.adopt);
  const referenceNode = useStoryStore((store) => store.referenceNode);
  const [creationTarget, setCreationTarget] = useState<StoryCreationTarget | null>(null);

  useEffect(() => {
    setCreationTarget(null);
    void load(workspaceId);
  }, [load, workspaceId]);

  const graph = state.graph;
  const placed = useMemo(() => (graph ? layout(graph) : null), [graph]);
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
            name: state.variants.find(variant => variant.id === state.variantId)?.name ?? "主方案",
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
      <header className="story-toolbar">
        <div role="tablist" aria-label="方案" className="story-variants">
          {state.variants.map((variant) => (
            <button
              key={variant.id}
              role="tab"
              aria-selected={variant.id === state.variantId}
              className="ui-button"
              data-primary={variant.primary ? "true" : undefined}
              onClick={() => void selectVariant(variant.id)}
            >
              {variant.name}
            </button>
          ))}
        </div>
        <div className="story-actions">
          <ReaderPreview key={`reader-${workspaceId}`} workspaceId={workspaceId} variantId={state.variantId ?? state.primaryId} revision={graph.revision} cutoff={state.nodeId} />
          <StoryCandidates key={workspaceId} workspaceId={workspaceId} />
          <Button
            size="compact"
            disabled={state.busy}
            onClick={() => void fork(`备选 ${state.variants.length + 1}`)}
            title="从当前方案复制一个备选，不影响主方案"
          >
            <GitFork size={16} strokeWidth={2} aria-hidden="true" />建备选
          </Button>
          {state.variantId !== state.primaryId && (
            <>
              <Button size="compact" onClick={() => void compare()}>
                与主方案比较
              </Button>
              <Button
                size="compact"
                disabled={state.busy}
                onClick={() => void adopt()}
                title="把整个备选的结构复制到主方案；正文关联与核对不复制"
              >
                <CheckCheck size={16} strokeWidth={2} aria-hidden="true" />采用此方案
              </Button>
              <Button size="compact" disabled={state.busy} onClick={() => void adopt([], false, "assist")}>采用并调整正文</Button>
            </>
          )}
          {state.lastChange && (
            <Button size="compact" disabled={state.busy} onClick={() => void undo()}>
              <Undo2 size={16} strokeWidth={2} aria-hidden="true" />撤销上一次
            </Button>
          )}
        </div>
      </header>

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

      {state.comparison && (
        <section className="story-comparison" aria-label="与主方案的差异">
          {(["nodes", "clues", "constraints", "edges", "event_times"] as const).map((kind) => {
            const added = state.comparison?.added[kind] ?? [];
            const removed = state.comparison?.removed[kind] ?? [];
            const updated = state.comparison?.updated[kind] ?? [];
            if (!added.length && !removed.length && !updated.length) return null;
            return (
              <p key={kind}>
                {kind}：新增 {added.length}、删除 {removed.length}、修改 {updated.length}
              </p>
            );
          })}
          <p className="story-hint">{state.comparison.note}</p>
        </section>
      )}

      {state.adoption && (
        <section className="story-adoption" aria-label="采用结果">
          <p>
            {state.adoption.conflict ? "这次没有复制任何东西：" : "已复制到主方案："}
            {Object.entries(state.adoption.plan.copied)
              .filter(([, items]) => items.length)
              .map(([kind, items]) => `${kind} ${items.length}`)
              .join("、") || "没有新增对象"}
            {state.adoption.plan.unchanged.length > 0 &&
              `；${state.adoption.plan.unchanged.length} 个对象已一致`}
          </p>
          {state.adoption.plan.pulled_in.length > 0 && (
            <p className="story-hint">
              为支撑所选内容一并带入：{state.adoption.plan.pulled_in.join("、")}
            </p>
          )}
          {state.adoption.plan.conflicts.length > 0 && (
            <>
              <p className="story-hint">目标方案里已有同 ID 但内容不同的对象：</p>
              <ul className="story-conflicts">
                {state.adoption.plan.conflicts.map((item) => (
                  <li key={`${item.kind}:${item.id}`}>
                    {item.kind}「{item.id}」：{item.reason}
                  </li>
                ))}
              </ul>
              <div className="story-adoption-actions">
                <Button size="compact" disabled={state.busy} onClick={() => void adopt(state.adoption?.plan.node_ids ?? [], true)}>
                  覆盖后重试
                </Button>
                <Button size="compact" disabled={state.busy} onClick={() => void adopt(state.adoption?.plan.node_ids ?? [], true, "assist")}>
                  覆盖并调整正文
                </Button>
              </div>
            </>
          )}
          {state.adoption.operationId && (
            <p className="story-hint">
              正文调整已受理；可在任务面板暂停、继续或插话。
            </p>
          )}
          <p className="story-hint">{state.adoption.plan.note}</p>
        </section>
      )}

      <div className="story-body">
        <div
          className="story-canvas"
          style={{ width: placed?.width, height: placed?.height }}
          role="group"
          aria-label="结构图"
        >
          <svg
            className="story-edges"
            width={placed?.width}
            height={placed?.height}
            aria-hidden="true"
          >
            {graph.edges.map((edge) => {
              const from = placed?.positions.get(edge.from_id);
              const to = placed?.positions.get(edge.to_id);
              if (!from || !to) return null;
              return (
                <path
                  key={edge.id}
                  d={edgePath(edge, from, to)}
                  data-kind={edge.kind}
                  className="story-edge"
                />
              );
            })}
          </svg>
          {graph.nodes.map((node) => {
            const spot = placed?.positions.get(node.id);
            if (!spot) return null;
            return (
              <button
                key={node.id}
                type="button"
                className="story-node"
                data-selected={node.id === state.nodeId ? "true" : undefined}
                style={{ left: spot.x, top: spot.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
                onClick={() => void selectNode(node.id === state.nodeId ? null : node.id)}
              >
                <span className="story-node-title">{node.title}</span>
                {node.clues.length > 0 && (
                  <span className="story-node-mark" aria-label={`${node.clues.length} 条线索`}>
                    {node.clues.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <aside className="story-detail" aria-label="节点详情">
          {!detail && <p className="story-hint">点一个节点看它的安排、线索与正文入口。</p>}
          {detail && (
            <>
              <h2>{detail.node.title}</h2>
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
                      {use.author_truth && (
                        <p className="story-truth">真相：{use.author_truth}</p>
                      )}
                      {use.expected_interpretation && (
                        <p className="story-hint">想引导：{use.expected_interpretation}</p>
                      )}
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
                {state.variantId !== state.primaryId && (
                  <><Button
                    size="compact"
                    disabled={state.busy}
                    onClick={() => void adopt([detail.node.id])}
                    title="只把这个节点与它的必需前提复制到主方案"
                  >
                    采用此节点
                  </Button><Button size="compact" disabled={state.busy}
                    onClick={() => void adopt([detail.node.id], false, "assist")}>
                    采用此节点并调整正文
                  </Button></>
                )}
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
