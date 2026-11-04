/**
 * 对话里的修订回执（v1.2）。
 *
 * 作者保存过正文、或让模型改过稿后，对话输入框上方显示最近一次修订：来源、时间与
 * 改动处数。回执只负责带回「本章修订」状态；分析与建议都在那里完成。
 */

import { useEffect, useState } from "react";

import { errorMessage } from "../../api/errors";
import { listRevisions, type RevisionSummary } from "../../api/revisions";
import { useRevisionPanelStore } from "../../state/revisions";

const SOURCE_LABEL: Record<string, string> = {
  author: "作者修订",
  model: "按作者要求改稿",
  candidate: "采用模型候选",
  author_after_model: "按作者要求改稿并由作者编辑",
};

function timeLabel(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export default function RevisionReceipt({
  workspaceId,
  chapterId,
  conversationId = null,
  refreshToken = 0,
}: {
  workspaceId: string;
  chapterId: string;
  conversationId?: string | null;
  refreshToken?: number;
}) {
  const [latest, setLatest] = useState<RevisionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = useRevisionPanelStore(state => state.open);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const page = await listRevisions(workspaceId, chapterId);
        if (!cancelled) {
          setLatest(page.items[0] ?? null);
          setError(null);
        }
      } catch (exc) {
        if (!cancelled) setError(errorMessage(exc, "修订回执"));
      }
    }
    void load();
    function onChanged(event: Event) {
      const detail = (event as CustomEvent<{ chapterId?: string }>).detail;
      if (!detail?.chapterId || detail.chapterId === chapterId) void load();
    }
    window.addEventListener("hastings:revision-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("hastings:revision-changed", onChanged);
    };
  }, [workspaceId, chapterId, refreshToken]);

  if (!latest) {
    return error ? (
      <p className="px-3 pb-1 text-[11px] text-[#8d3a2a]">{error}</p>
    ) : null;
  }

  return (
    <button
      type="button"
      aria-label="查看差异"
      className="mx-3 mb-2 flex w-[calc(100%-1.5rem)] items-center gap-2 rounded-sm border border-[#d8cfb2] bg-[#fffdf6] px-2.5 py-2 text-left text-[11px] hover:bg-[#f1ead7]"
      onClick={() =>
        open({
          workspaceId,
          chapterId,
          revisionId: latest.id,
          tab: "revisions",
          conversationId,
        })
      }
    >
      <span className="font-semibold text-sheetInk">最近修订</span>
      <span className="min-w-0 flex-1 truncate text-mute">
        {SOURCE_LABEL[latest.source] ?? latest.source} · {timeLabel(latest.confirmed_at)} · {latest.hunk_count} 处
        {latest.analyzable ? "" : " · 已不在当前稿"}
      </span>
      <span className="shrink-0 text-[#6b5d3e]">查看 →</span>
    </button>
  );
}
