/**
 * 对话里的修订回执（v1.2）。
 *
 * 作者保存过正文、或让模型改过稿后，对话输入框上方显示最近一次修订：来源、时间、
 * 改动处数，以及「查看差异」「让模型分析」。它不是临时提示——刷新后仍按当前章节
 * 重新读取，分析的运行日志与建议显示在对话里。
 */

import { useEffect, useState } from "react";

import { errorMessage } from "../../api/errors";
import { listRevisions, type RevisionSummary } from "../../api/revisions";
import { useRevisionPanelStore } from "../../state/revisions";
import { Button } from "../ui/Button";

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
    <div className="mx-3 mb-2 rounded-sm border border-[#d8cfb2] bg-[#fffdf6] px-2.5 py-2 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-sheetInk">
          {SOURCE_LABEL[latest.source] ?? latest.source}
        </span>
        <span className="text-mute">
          {timeLabel(latest.confirmed_at)} · {latest.hunk_count} 处
          {latest.analyzable ? "" : " · 已不在当前稿"}
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <Button
          size="compact"
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
          查看差异
        </Button>
        <Button
          size="compact"
          variant="primary"
          disabled={!latest.analyzable}
          disabledReason="这次改动已经不在当前正文里，只保留历史"
          onClick={() =>
            open({
              workspaceId,
              chapterId,
              revisionId: latest.id,
              tab: "revisions",
              autoAnalyze: true,
              conversationId,
            })
          }
        >
          让模型分析
        </Button>
      </div>
    </div>
  );
}
