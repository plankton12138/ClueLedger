/**
 * 对账同步卡片（P5 T5，设计稿 C 态）：notice 驱动，附在对话流末尾。
 * V1 弃用后（§2.10）后端不再产生对账 notice、WS sync 命令已下线，
 * 此组件仅作展示残留保留：syncCards 队列在生产中恒为空。
 */

import { chapterNumber } from "../state/chapter";
import type { SyncCardEntry } from "../state/store";

export default function SyncCard({ entry }: { entry: SyncCardEntry }) {
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
        <p className="mt-2 text-[11px] text-paper/50">正文与账本未受影响。</p>
      )}
    </div>
  );
}
