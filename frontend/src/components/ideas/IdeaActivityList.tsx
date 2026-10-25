/**
 * 灵感的使用记录（0.8 I5）。
 *
 * 分开显示三件事：在哪次讨论里被带上、在哪次对话里展开过、有没有真的写进正文。
 * 「写入」只有在当前正文的指纹与区间都能对上、并且那次提交没有被撤销时才算有效；
 * 路径还在不代表引文还在。索引没扫完时不显示「没有使用记录」，而是说明还在整理。
 */

import { useEffect } from "react";

import {
  type IdeaActivity,
  type IdeaActivityApplied,
  type IdeaActivityLinked,
  type IdeaActivityTurn,
} from "../../api/ideas";
import { useIdeaActivityStore } from "../../state/ideaActivity";
import { useIdeaScope } from "../../state/ideas";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

const KIND_LABEL: Record<string, string> = {
  referenced: "在讨论中引用",
  expanded: "展开讨论",
  applied: "写入正文",
  linked: "关联",
};

function when(value: string): string {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function validityLabel(value: string): string {
  if (value === "current") return "当前仍可核对";
  if (value === "reverted") return "该次修改已撤回";
  return "该位置已变更";
}

interface Props {
  onOpenConversation?: (conversationId: string) => void;
  onOpenSource?: (path: string) => void;
}

export default function IdeaActivityList({ onOpenConversation, onOpenSource }: Props) {
  const scope = useIdeaScope();
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const ideaId = scope.activeId;
  const items = useIdeaActivityStore(state => state.items);
  const total = useIdeaActivityStore(state => state.total);
  const cursor = useIdeaActivityStore(state => state.cursor);
  const complete = useIdeaActivityStore(state => state.complete);
  const loading = useIdeaActivityStore(state => state.loading);
  const error = useIdeaActivityStore(state => state.error);
  const load = useIdeaActivityStore(state => state.load);
  const more = useIdeaActivityStore(state => state.more);
  const reset = useIdeaActivityStore(state => state.reset);

  useEffect(() => {
    if (!workspaceId || !ideaId) {
      reset();
      return;
    }
    void load(workspaceId, ideaId);
  }, [workspaceId, ideaId, load, reset]);

  if (!ideaId) return null;

  return (
    <details className="idea-activity" open={items.length > 0}>
      <summary>使用记录{total > 0 ? `（${total}）` : ""}</summary>
      <div className="idea-history-content">
        {error && <StatusMessage tone="error">{error}</StatusMessage>}
        {loading && items.length === 0 && <StatusMessage>正在读取使用记录…</StatusMessage>}
        {!loading && !complete && (
          <p className="ui-field-hint">使用记录还在整理，下面可能还不完整。</p>
        )}
        {!loading && complete && items.length === 0 && !error && (
          <p className="ui-field-hint">还没有在任何讨论或写入中使用过这条灵感。</p>
        )}
        <ul>
          {items.map(item => (
            <li key={item.id}>
              <span>
                {KIND_LABEL[item.kind] ?? "记录"} · {when(item.at)}
              </span>
              <ActivityTarget
                item={item}
                onOpenConversation={onOpenConversation}
                onOpenSource={onOpenSource}
              />
            </li>
          ))}
        </ul>
        {(!complete || cursor) && (
          <Button size="compact" variant="quiet" busy={loading} onClick={() => void more()}>
            {complete ? "继续读取" : "继续整理"}
          </Button>
        )}
        <Button
          size="compact"
          variant="quiet"
          disabled={loading}
          onClick={() => workspaceId && ideaId && void load(workspaceId, ideaId)}
        >
          刷新记录
        </Button>
      </div>
    </details>
  );
}

function ActivityTarget({
  item,
  onOpenConversation,
  onOpenSource,
}: {
  item: IdeaActivity;
  onOpenConversation?: (conversationId: string) => void;
  onOpenSource?: (path: string) => void;
}) {
  if (item.kind === "referenced" || item.kind === "expanded") {
    const turn = item as IdeaActivityTurn;
    // 只有明确说不可用才拦住跳转；缺字段时按可用处理，不把记录误判成失效。
    if (turn.conversation_available === false) {
      return <span className="ui-field-hint">这次讨论的会话已不可用</span>;
    }
    return (
      <Button size="compact" variant="quiet" onClick={() => onOpenConversation?.(turn.conversation_id)}>
        {turn.conversation_title || "打开讨论"}
      </Button>
    );
  }
  if (item.kind === "applied") {
    const applied = item as IdeaActivityApplied;
    return (
      <span className="idea-activity-target">
        <Button size="compact" variant="quiet" onClick={() => onOpenSource?.(applied.target_path)}>
          {applied.target_path}
        </Button>
        <span className="ui-field-hint">{validityLabel(applied.validity)}</span>
      </span>
    );
  }
  return (
    <span className="ui-field-hint">{(item as IdeaActivityLinked).target_path || "关联条目"}</span>
  );
}
