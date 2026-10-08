/**
 * 主页面（P3 T8）：头部状态条（连接状态 / 写作中 / slug·model）+ 左对话流 +
 * 右故事账本占位（T9 填充 BiblePanel）。断线时显示重连入口——手动重连，
 * 不做自动重连风暴（计划 T8）。
 */

import ChatStream from "./components/ChatStream";
import Composer from "./components/Composer";
import type { CommandType } from "./api/types";
import type { ConnectionStatus } from "./api/socket";
import { useChatStore } from "./state/store";
import { useAgentSocket } from "./state/useAgentSocket";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "连接中",
  open: "已连接",
  closed: "已断开",
};

const STATUS_DOT: Record<ConnectionStatus, string> = {
  connecting: "bg-planned",
  open: "bg-planted",
  closed: "bg-overdue",
};

export default function App() {
  const { reconnect, sendCommand } = useAgentSocket();
  const status = useChatStore((s) => s.status);
  const connectionError = useChatStore((s) => s.connectionError);
  const running = useChatStore((s) => s.running);
  const snapshot = useChatStore((s) => s.snapshot);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-4 border-b border-line px-6 py-3">
        <h1 className="font-serif text-xl font-bold text-gold">黑斯廷斯备忘录</h1>
        <p className="hidden text-xs text-gold-soft/60 sm:block">
          Hastings&rsquo; Memo · 会记账的悬疑小说 AI 主编
        </p>
        <div className="ml-auto flex items-center gap-3 text-xs text-paper/60">
          {snapshot && (
            <span className="hidden font-mono md:inline">
              {snapshot.slug} · {snapshot.model}
            </span>
          )}
          {running && (
            <span className="border border-planned/50 px-2 py-0.5 text-planned">写作中</span>
          )}
          <span className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full ${STATUS_DOT[status]} ${
                status !== "open" ? "animate-pulse" : ""
              }`}
            />
            {STATUS_LABEL[status]}
          </span>
          {status === "closed" && (
            <button
              type="button"
              onClick={reconnect}
              className="border border-line px-2 py-0.5 transition-colors hover:border-gold/50"
            >
              重连
            </button>
          )}
        </div>
      </header>

      {connectionError && (
        <div className="flex items-center gap-3 border-b border-overdue/40 bg-overdue/10 px-6 py-2 text-xs text-paper">
          <span className="min-w-0 flex-1">{connectionError}</span>
          <button
            type="button"
            onClick={reconnect}
            className="shrink-0 border border-overdue/60 px-2 py-0.5 text-overdue transition-colors hover:bg-overdue/10"
          >
            重连
          </button>
        </div>
      )}

      <main className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col border-r border-line">
          <ChatStream />
          <Composer
            sendCommand={(type: CommandType, message: string) => sendCommand(type, message)}
            abort={() => sendCommand("abort")}
          />
        </section>
        <aside className="hidden w-80 shrink-0 overflow-y-auto p-4 lg:block">
          <h2 className="font-serif text-base text-gold">故事账本</h2>
          <p className="mt-2 text-xs leading-relaxed text-paper/40">
            五账本面板在 T9 接入（/api/bible 只读面已就绪）。
          </p>
        </aside>
      </main>
    </div>
  );
}
