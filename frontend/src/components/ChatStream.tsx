/**
 * 对话流（P3 T8，P7 T3 工具卡片化）：消息气泡、工具卡片（头部=工具名+状态；
 * 完成后直接展示 details 摘要行，展开看原始参数/结果——复用既有
 * ToolExecutionStart/End 帧数据，零事件契约变更）、错误卡片、流式 buffer。
 * 数据全部来自 store，本组件不跑任何 Agent 逻辑。
 */

import { useEffect, useRef, useState } from "react";
import type { ChatItem, ToolCardStatus } from "../state/reducer";
import { toolDetailLines } from "../state/toolSummary";
import { useChatStore } from "../state/store";
import SyncCard from "./SyncCard";

const TOOL_STATUS_CLASS: Record<ToolCardStatus, string> = {
  running: "bg-planned",
  ok: "bg-planted",
  error: "bg-overdue",
};

const TOOL_STATUS_LABEL: Record<ToolCardStatus, string> = {
  running: "运行中",
  ok: "完成",
  error: "失败",
};

const TOOL_STATUS_TEXT: Record<ToolCardStatus, string> = {
  running: "text-planned",
  ok: "text-planted",
  error: "text-overdue",
};

function truncate(text: string, max = 120): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 折叠态展示结果首行（write_chapter 的全文很长，只露一行）。 */
function firstLine(text: string): string {
  const line = text.split("\n").find((l) => l.trim().length > 0) ?? "";
  return truncate(line.trim());
}

type ToolItem = Extract<ChatItem, { kind: "tool" }>;

function ToolCard({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false);
  const summary = firstLine(item.resultText) || truncate(JSON.stringify(item.args), 60);
  // 完成后的 details 摘要行（D4）：不展开也能看到字段级结果；无 details 时不渲染。
  const detailLines = item.status === "ok" ? toolDetailLines(item.resultDetails) : [];
  return (
    <div className="max-w-[90%] border border-line bg-ink-soft/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${TOOL_STATUS_CLASS[item.status]} ${
            item.status === "running" ? "animate-pulse" : ""
          }`}
        />
        <span className="font-mono text-xs text-gold">{item.toolName}</span>
        <span className={`shrink-0 text-[10px] ${TOOL_STATUS_TEXT[item.status]}`}>
          {TOOL_STATUS_LABEL[item.status]}
        </span>
        <span className="ml-auto shrink-0 text-xs text-paper/30">{open ? "收起" : "详情"}</span>
      </button>
      {(item.status === "ok" || item.status === "error") && (
        <div className="border-t border-line/60 px-3 py-1.5 text-xs">
          {detailLines.length > 0 ? (
            <ul className="space-y-0.5">
              {detailLines.map((line) => (
                <li key={line.label} className="flex gap-2">
                  <span className="shrink-0 text-gold-soft">{line.label}</span>
                  <span className="min-w-0 flex-1 break-all text-paper/70">{line.value}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={item.status === "error" ? "text-overdue" : "text-paper/50"}>
              {summary || "（无文本结果）"}
            </p>
          )}
        </div>
      )}
      {item.status === "running" && (
        <p className="border-t border-line/60 px-3 py-1.5 text-xs text-paper/40">执行中…</p>
      )}
      {open && (
        <div className="space-y-2 border-t border-line px-3 py-2 text-xs">
          <div>
            <p className="text-gold-soft">调用参数</p>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-paper/70">
              {JSON.stringify(item.args, null, 2)}
            </pre>
          </div>
          <div>
            <p className="text-gold-soft">结果</p>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-paper/70">
              {item.resultText || "（无文本结果）"}
            </pre>
          </div>
          {item.resultDetails !== null && item.resultDetails !== undefined && (
            <div>
              <p className="text-gold-soft">结构化详情</p>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-paper/70">
                {JSON.stringify(item.resultDetails, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ItemView({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case "user":
      return (
        <div className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap break-words border border-planned/40 bg-planned/10 px-4 py-2 text-sm text-paper">
            {item.text}
          </div>
        </div>
      );
    case "assistant":
      return (
        <div className="max-w-[85%] border-l-2 border-gold bg-ink-soft px-4 py-2">
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-paper">{item.text}</p>
        </div>
      );
    case "thinking":
      return (
        <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
          <p className="text-xs text-gold-soft/70">思路</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs italic leading-relaxed text-paper/50">
            {item.text}
          </p>
        </div>
      );
    case "tool":
      return <ToolCard item={item} />;
    case "error":
      return (
        <div className="max-w-[85%] border border-overdue/50 bg-overdue/10 px-4 py-2">
          <p className="text-xs text-overdue">错误</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-paper">{item.text}</p>
        </div>
      );
  }
}

export default function ChatStream({
  onRetrySync,
}: {
  /** 对账失败卡片的重试回调（经 WS sync 命令手动重跑）。 */
  onRetrySync?: (chapter: string) => void;
} = {}) {
  const items = useChatStore((s) => s.items);
  const syncCards = useChatStore((s) => s.syncCards);
  const assistantBuffer = useChatStore((s) => s.assistantBuffer);
  const thinkingBuffer = useChatStore((s) => s.thinkingBuffer);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items, syncCards, assistantBuffer, thinkingBuffer]);

  const empty = items.length === 0 && syncCards.length === 0 && !assistantBuffer && !thinkingBuffer;
  return (
    <div className="flex-1 space-y-3 overflow-y-auto px-6 py-4">
      {empty && (
        <p className="mt-24 text-center text-sm text-paper/40">
          向主编下达第一个指令——先建两个角色档案，再写第一章。
        </p>
      )}
      {items.map((item, i) => (
        <ItemView key={i} item={item} />
      ))}
      {syncCards.map((card, i) => (
        <SyncCard key={`sync-${i}`} entry={card} onRetry={onRetrySync} />
      ))}
      {thinkingBuffer && (
        <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
          <p className="text-xs text-gold-soft/70">思路（流式中）</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs italic text-paper/50">{thinkingBuffer}</p>
        </div>
      )}
      {assistantBuffer && (
        <div className="max-w-[85%] border-l-2 border-gold bg-ink-soft px-4 py-2">
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-paper">
            {assistantBuffer}
            <span className="animate-pulse text-gold">▍</span>
          </p>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
