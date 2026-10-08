/**
 * 对话流（P3 T8）：消息气泡、工具卡片（折叠单行摘要，计划 D8）、错误卡片、
 * 流式 buffer 实时区。数据全部来自 store，本组件不跑任何 Agent 逻辑。
 */

import { useEffect, useRef, useState } from "react";
import type { ChatItem, ToolCardStatus } from "../state/reducer";
import { useChatStore } from "../state/store";

const TOOL_STATUS_CLASS: Record<ToolCardStatus, string> = {
  running: "bg-planned",
  ok: "bg-planted",
  error: "bg-overdue",
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
        <span className="min-w-0 flex-1 truncate text-xs text-paper/50">{summary}</span>
        <span className="shrink-0 text-xs text-paper/30">{open ? "收起" : "展开"}</span>
      </button>
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
          <div className="max-w-[85%] whitespace-pre-wrap border border-planned/40 bg-planned/10 px-4 py-2 text-sm text-paper">
            {item.text}
          </div>
        </div>
      );
    case "assistant":
      return (
        <div className="max-w-[85%] border-l-2 border-gold bg-ink-soft px-4 py-2">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-paper">{item.text}</p>
        </div>
      );
    case "thinking":
      return (
        <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
          <p className="text-xs text-gold-soft/70">思路</p>
          <p className="mt-1 whitespace-pre-wrap text-xs italic leading-relaxed text-paper/50">
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

export default function ChatStream() {
  const items = useChatStore((s) => s.items);
  const assistantBuffer = useChatStore((s) => s.assistantBuffer);
  const thinkingBuffer = useChatStore((s) => s.thinkingBuffer);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items, assistantBuffer, thinkingBuffer]);

  const empty = items.length === 0 && !assistantBuffer && !thinkingBuffer;
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
      {thinkingBuffer && (
        <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
          <p className="text-xs text-gold-soft/70">思路（流式中）</p>
          <p className="mt-1 whitespace-pre-wrap text-xs italic text-paper/50">{thinkingBuffer}</p>
        </div>
      )}
      {assistantBuffer && (
        <div className="max-w-[85%] border-l-2 border-gold bg-ink-soft px-4 py-2">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-paper">
            {assistantBuffer}
            <span className="animate-pulse text-gold">▍</span>
          </p>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
