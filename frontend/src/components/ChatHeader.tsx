/**
 * 对话面板头部（P9 T6，D4）：「新会话」入口 + 内联防误触确认。
 * 确认风格对齐文章删除：说明后果（旧对话归档保留、正文/账本不动）+
 * 显式确认键；写作中按钮禁用（服务端另有 409 硬守卫）。
 */

import { useState } from "react";
import { resetSession } from "../api/rest";

type Mode = "idle" | "confirm" | "busy";

export default function ChatHeader({
  slug,
  running,
  onResetDone,
}: {
  slug: string;
  running: boolean;
  /** reset 成功后回调（App 触发 WS 重连，快照重放得到空会话）。 */
  onResetDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>("idle");
  const [error, setError] = useState<string | null>(null);

  const handleReset = async () => {
    setMode("busy");
    setError(null);
    try {
      await resetSession(slug);
      setMode("idle");
      onResetDone();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setMode("confirm");
    }
  };

  if (mode === "confirm" || mode === "busy") {
    return (
      <div className="flex-none border-b border-line bg-ink-soft/60 px-3 py-2">
        <p className="text-xs leading-relaxed text-paper/70">
          开新会话后，旧对话将归档保留在磁盘（可找回），
          <span className="text-gold-soft">正文与故事账本不受影响</span>。
          {running && <span className="text-overdue">写作中不可开新会话。</span>}
        </p>
        {error && <p className="mt-1.5 break-all text-xs text-overdue">{error}</p>}
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            disabled={mode === "busy" || running}
            onClick={() => void handleReset()}
            className="border border-gold/60 px-2.5 py-1 text-xs text-gold transition-colors hover:bg-gold/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {mode === "busy" ? "归档中…" : "确认开新会话"}
          </button>
          <button
            type="button"
            disabled={mode === "busy"}
            onClick={() => {
              setMode("idle");
              setError(null);
            }}
            className="border border-line px-2.5 py-1 text-xs text-paper/60 transition-colors hover:text-paper"
          >
            取消
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-none items-center justify-between gap-2 border-b border-line bg-ink-soft/60 px-3 py-1.5">
      <span className="truncate text-xs tracking-widest text-gold-soft">主编对话</span>
      <button
        type="button"
        disabled={running}
        title={running ? "写作中不可开新会话" : "归档当前对话并开新会话"}
        onClick={() => setMode("confirm")}
        className="flex-none border border-line px-2 py-0.5 text-[11px] text-paper/70 transition-colors hover:border-gold/60 hover:text-gold disabled:cursor-not-allowed disabled:opacity-40"
      >
        新会话
      </button>
    </div>
  );
}
