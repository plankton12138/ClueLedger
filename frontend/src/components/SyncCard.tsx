/**
 * 对账同步卡片（P5 T5，设计稿 C 态）：notice 驱动，附在对话流末尾。
 * 成功 → 完成明细（检查 N 处变更 / 更新 M 处）；失败 → 归因 + 「重新同步」
 * （经 WS sync 命令手动重跑，复用最近一次保存的差异）。
 */

import { chapterNumber } from "../state/chapter";
import type { SyncCardEntry } from "../state/store";

export default function SyncCard({
  entry,
  onRetry,
}: {
  entry: SyncCardEntry;
  onRetry?: (chapter: string) => void;
}) {
  const failed = entry.status === "failed";
  return (
    <div
      className={`max-w-[90%] border px-3 py-2 ${
        failed ? "border-overdue/50 bg-overdue/10" : "border-gold/45 bg-gold/[0.07]"
      }`}
    >
      <p className={`flex items-center gap-2 text-xs font-semibold ${failed ? "text-overdue" : "text-gold"}`}>
        ◈ 对账{failed ? "失败" : "完成"} · 第{chapterNumber(entry.chapter)}章
      </p>
      <p className="mt-1.5 text-xs leading-relaxed text-paper/70">
        检查 <b className="text-gold-soft">{entry.hunks}</b> 处段落变更，账本更新{" "}
        <b className="text-gold-soft">{entry.updates}</b> 处
      </p>
      {failed && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-[11px] text-paper/50">
            正文与账本未受影响；可手动重跑对账。
          </span>
          {onRetry && (
            <button
              type="button"
              onClick={() => onRetry(entry.chapter)}
              className="shrink-0 border border-overdue/60 px-2 py-0.5 text-[11px] text-overdue transition-colors hover:bg-overdue/10"
            >
              重新同步
            </button>
          )}
        </div>
      )}
    </div>
  );
}