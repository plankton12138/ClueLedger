/**
 * 作者确认交互卡（P10 T10，D9）：把模型输出的【确认】标记块渲染为结构化卡片——
 * 说明行 + 「确认」「暂不」两键，点击发送定式指令。硬约束仍在 story_stage
 * 工具层，本卡片只是 UI 便利层（协议软、工具硬）。
 */

import { CONFIRM_REPLY, DECLINE_REPLY, type ChatConfirm } from "./confirm";
import ActionButton from "./ActionButton";

export default function ConfirmCard({
  confirm,
  onRespond,
}: {
  confirm: ChatConfirm;
  /** 点击确认/暂不后的发送回调（App 走 WS 发送定式指令）。 */
  onRespond?: (message: string) => void;
}) {
  return (
    <div className="mt-2 border border-gold/40 bg-gold/5 px-3 py-2">
      <p className="font-mono text-[10px] tracking-widest text-gold-soft/70">待作者确认</p>
      <dl className="mt-1.5 space-y-1 text-xs">
        <div className="flex gap-2">
          <dt className="shrink-0 text-gold-soft">待确认</dt>
          <dd className="min-w-0 flex-1 break-words text-paper/80">{confirm.pending}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-gold-soft">确认后</dt>
          <dd className="min-w-0 flex-1 break-words text-paper/70">{confirm.after}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-gold-soft">暂不</dt>
          <dd className="min-w-0 flex-1 break-words text-paper/70">{confirm.decline}</dd>
        </div>
      </dl>
      <div className="mt-2 flex flex-wrap gap-2">
        <ActionButton label="确认" onClick={() => onRespond?.(CONFIRM_REPLY)} />
        <ActionButton label="暂不" onClick={() => onRespond?.(DECLINE_REPLY)} />
      </div>
    </div>
  );
}