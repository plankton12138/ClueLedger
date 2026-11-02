/**
 * 构思页：默认按讲述位置阅读情节安排，时间轴按需进入。
 *
 * 页面只读：节点详情、正文出处与「讨论调整」在右侧共享详情区；这里不提供名称或用途
 * 表单，创建也直接进入对话。
 */

import { useEffect, useMemo, useState } from "react";
import { Clock3, MessageSquare, Undo2 } from "lucide-react";

import StoryDraft from "./StoryDraft";
import StoryBatches from "./StoryBatches";
import StoryTimeline from "./StoryTimeline";
import type { StoryNode, StoryTimeline as StoryTimelineView } from "../../api/story";
import { draftKeyOf, useConversationsStore } from "../../state/conversations";
import { useLayoutStore } from "../../state/layout";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";

const STATUS_LABEL: Record<string, string> = {
  ready: "就绪",
  draft: "草稿",
  attention: "需核对",
};

interface OverviewGroup {
  key: string;
  title: string;
  hint: string;
  nodes: StoryNode[];
}

export function groupNodes(nodes: StoryNode[], timeline: StoryTimelineView | null): OverviewGroup[] {
  const groups = new Map<string, OverviewGroup>();
  const placed = new Set<string>();
  if (timeline) {
    for (const group of timeline.groups) {
      const rows = group.nodes
        .map(row => nodes.find(node => node.id === row.node_id))
        .filter((node): node is StoryNode => Boolean(node));
      if (rows.length === 0) continue;
      for (const node of rows) placed.add(node.id);
      groups.set(`${group.chapter_kind}:${group.chapter_number ?? "none"}`, {
        key: `${group.chapter_kind}:${group.chapter_number ?? "none"}`,
        title: group.title,
        hint: group.chapter_kind === "planned" ? "计划" : group.chapter_kind === "unassigned" ? "未安排章节" : "正文已出现",
        nodes: rows,
      });
    }
  }
  const rest = nodes.filter(node => !placed.has(node.id));
  if (timeline && groups.size > 0) {
    if (rest.length > 0) {
      groups.set("rest", { key: "rest", title: "尚未安排章节", hint: "未安排章节", nodes: rest });
    }
  } else {
    // 时间轴不可用时只按作者明确给出的计划章节分组，不按标题猜归属。
    for (const node of rest) {
      const key = node.planned_chapter_number ? `planned:${node.planned_chapter_number}` : "rest";
      const current = groups.get(key);
      if (current) {
        current.nodes.push(node);
      } else {
        groups.set(key, {
          key,
          title: node.planned_chapter_number ? `第 ${node.planned_chapter_number} 章` : "尚未安排章节",
          hint: node.planned_chapter_number ? "计划" : "未安排章节",
          nodes: [node],
        });
      }
    }
  }
  return [...groups.values()];
}

export default function StorySurface({
  workspaceId,
  onStart,
  onCalibrate,
  onSelectNode,
}: {
  workspaceId: string;
  /** 创建请求受理后打开对话，显示实际任务进度。 */
  onStart: () => void;
  /** 根据已有正文校准当前构思；不会直接替换正式结构。 */
  onCalibrate: () => void;
  onSelectNode: (nodeId: string | null) => void;
}) {
  const state = useStoryStore();
  const load = useStoryStore((store) => store.load);
  const undo = useStoryStore((store) => store.undo);
  const [showTimeline, setShowTimeline] = useState(false);

  useEffect(() => {
    setShowTimeline(false);
    void load(workspaceId);
  }, [load, workspaceId]);

  const graph = state.graph;
  const groups = useMemo(
    () => groupNodes(graph?.nodes ?? [], state.timeline),
    [graph?.nodes, state.timeline],
  );
  const attention = useMemo(
    () => new Map((state.attention?.nodes ?? []).map(row => [row.node_id, row])),
    [state.attention?.nodes],
  );

  function discussCreation() {
    const chat = useConversationsStore.getState();
    const key = draftKeyOf(chat);
    // 未发送的文字不能被创建入口覆盖：只在输入区为空时放入建议要求。
    if (!(chat.drafts[key] ?? "").trim()) {
      chat.setDraft(
        "创建构思：\n请根据当前对话和已装入的资料，建立实际时间线、节点、线索与连接。先写清每条线索的表面读法、作者真相、埋设与回收；未确定的真相保持未定。",
      );
    }
    useLayoutStore.getState().revealChat();
    onStart();
  }

  if (state.loading && !graph) {
    return <div className="story-surface story-surface-empty">正在读取构思…</div>;
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="story-surface story-surface-empty">
        <StoryDraft key={workspaceId} workspaceId={workspaceId} />
        {state.error ? (
          <StatusMessage
            tone="error"
            action={<Button onClick={() => void load(workspaceId)}>重新读取</Button>}
          >
            {state.error}
          </StatusMessage>
        ) : (
          <EmptyState action={<Button variant="primary" onClick={discussCreation}>在对话里创建构思</Button>}>
            还没有构思。在对话里说明谜题、真相与线索；创建进度会显示在对话中。
          </EmptyState>
        )}
      </div>
    );
  }

  return (
    <div className="story-surface">
      <header className="story-toolbar">
        <div className="story-overview-title">
          <h1>{showTimeline ? "故事时间轴" : "情节安排"}</h1>
          <p>
            {showTimeline
              ? "横轴是发生时间，纵向按披露章节分组；选中节点在右侧查看。"
              : "按讲述位置排列；章节和时间只在作者明确时显示。"}
          </p>
        </div>
        <div className="story-actions">
          <Button
            size="compact"
            aria-pressed={showTimeline}
            onClick={() => setShowTimeline(value => !value)}
          >
            <Clock3 size={16} strokeWidth={2} aria-hidden="true" />
            {showTimeline ? "返回安排列表" : "查看时间轴"}
          </Button>
          {state.lastChange && (
            <Button size="compact" disabled={state.busy} onClick={() => void undo()}>
              <Undo2 size={16} strokeWidth={2} aria-hidden="true" />撤销上一次
            </Button>
          )}
          <Button size="compact" variant="quiet" disabled={state.busy} onClick={onCalibrate}>
            从正文校准
          </Button>
          <Button size="compact" variant="quiet" onClick={discussCreation}>
            <MessageSquare size={15} aria-hidden="true" />在对话里调整
          </Button>
        </div>
      </header>

      <StoryDraft key={`story-draft-${workspaceId}`} workspaceId={workspaceId} />
      <StoryBatches key={`story-batches-${workspaceId}`} workspaceId={workspaceId} />

      {state.error && (
        <p className="story-error" role="alert">
          {state.error}
          <button type="button" className="ui-button" onClick={() => state.dismissError()}>
            知道了
          </button>
        </p>
      )}

      <div className="story-body">
        {showTimeline ? (
          <StoryTimeline
            timeline={state.timeline}
            selectedNodeId={state.nodeId}
            onSelectNode={onSelectNode}
          />
        ) : (
          <div className="story-overview" role="list" aria-label="情节安排">
            {groups.map(group => (
              <section key={group.key} className="story-overview-group" role="listitem" aria-label={group.title}>
                <header className="story-overview-group-head">
                  <h2>{group.title}</h2>
                  <span>{group.hint}</span>
                </header>
                {group.nodes.map(node => {
                  const row = attention.get(node.id);
                  return (
                    <button
                      key={node.id}
                      type="button"
                      className="story-overview-row"
                      aria-pressed={node.id === state.nodeId}
                      onClick={() => onSelectNode(node.id)}
                    >
                      <span className={`story-overview-status ${row?.status ?? "draft"}`}>
                        {STATUS_LABEL[row?.status ?? "draft"] ?? "草稿"}
                      </span>
                      <span className="story-overview-title">{node.title}</span>
                      <span className="story-overview-action">{node.action}</span>
                      {node.time_note && <span className="story-overview-time">{node.time_note}</span>}
                    </button>
                  );
                })}
              </section>
            ))}
            {groups.length === 0 && (
              <p className="workspace-detail-hint">还没有节点。可以在对话里说明要安排的情节。</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
