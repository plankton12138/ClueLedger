/**
 * 对话流（P3 T8，P7 T3 工具卡片化）：消息气泡、工具卡片（头部=工具名+状态；
 * 完成后直接展示 details 摘要行，展开看原始参数/结果——复用既有
 * ToolExecutionStart/End 帧数据，零事件契约变更）、错误卡片、流式 buffer。
 * 数据全部来自 store，本组件不跑任何 Agent 逻辑。
 */

import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatItem, ToolCardStatus } from "../state/reducer";
import { toolDetailLines } from "../state/toolSummary";
import { useChatStore } from "../state/store";
import SyncCard from "./SyncCard";
import ThinkingCard from "./ThinkingCard";

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

/**
 * Agent 消息 Markdown 渲染（P8 T7，D7）：react-markdown + remark-gfm——
 * GFM 表格是缺口清单/对比表的刚需；组件纯函数无副作用，流式增量安全。
 * 样式走任意变体（无 typography 插件依赖）；memo 化降低长对话重渲成本。
 */
const MD_CLASS =
  "min-w-0 break-words text-sm leading-relaxed text-paper " +
  "[&_h1]:mb-1 [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-bold " +
  "[&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:text-sm [&_h2]:font-bold " +
  "[&_h3]:mb-1 [&_h3]:mt-1.5 [&_h3]:text-sm [&_h3]:font-semibold " +
  "[&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:my-0.5 " +
  "[&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-paper/70 " +
  "[&_code]:rounded [&_code]:bg-ink [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs " +
  "[&_pre]:overflow-x-auto [&_pre]:bg-ink [&_pre]:p-2 [&_pre]:text-xs " +
  "[&_table]:w-full [&_table]:border-collapse [&_table]:text-xs " +
  "[&_th]:border [&_th]:border-line [&_th]:bg-ink/60 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left " +
  "[&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1";

const AgentMarkdown = memo(function AgentMarkdown({ text }: { text: string }) {
  return (
    <div className={MD_CLASS}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
});

/** Agent 消息头部标签（D7 区分度）：与用户消息的右对齐异色底形成对照。 */
function AgentLabel() {
  return (
    <p className="mb-1 font-mono text-[10px] tracking-widest text-gold-soft/70">HASTINGS</p>
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
          <AgentLabel />
          <AgentMarkdown text={item.text} />
        </div>
      );
    case "thinking":
      return <ThinkingCard text={item.text} />;
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
      {thinkingBuffer && <ThinkingCard text={thinkingBuffer} streaming />}
      {assistantBuffer && (
        <div className="max-w-[85%] border-l-2 border-gold bg-ink-soft px-4 py-2">
          <AgentLabel />
          <div className={MD_CLASS}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{assistantBuffer}</ReactMarkdown>
            <span className="animate-pulse text-gold">▍</span>
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
