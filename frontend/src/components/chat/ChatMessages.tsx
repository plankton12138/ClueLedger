/**
 * 消息列表（§3.2/§3.3）：流式文本、引用来源、实际模型、失败/中断状态。
 *
 * - 阅读较早消息时不强制滚底，显示「有新回复」入口；接近底部时跟随输出；
 * - 任务按钮只来自结构化 payload（见 ChatCards）；
 * - 助手文本消息提供「基于此回复准备任务」——进入同一 TaskDraft，不直接启动。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ChatMessage, ChatReference, TaskKind } from "../../api/chat";
import { taskKindLabels } from "../../api/chat";
import {
  activeAbandoned,
  activeMessages,
  activeTurnId,
  activeTurns,
  activeView,
  useChatStore,
} from "../../state/chat";
import ChatMarkdown from "./ChatMarkdown";
import {
  CandidateCard,
  ResultCard,
  TaskDraftCard,
  TaskExecutionCard,
} from "./ChatCards";

function ReferenceChips({ refs }: { refs: ChatReference[] }) {
  if (refs.length === 0) return null;
  return (
    <ul className="chat-refs-used" aria-label="本轮引用">
      {refs.map((r) => (
        <li key={r.ref_id}>
          <span className={`chat-ref-chip ${r.saved ? "" : "unsaved"}`}>
            {r.title}
            {r.saved ? ` · ${r.version.slice(0, 8)}` : " · 未保存摘录"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function TextMessage({ message }: { message: ChatMessage }) {
  const draftFromMessage = useChatStore((s) => s.draftFromMessage);
  const retry = useChatStore((s) => s.retry);
  const setDraft = useChatStore((s) => s.setDraft);
  const [menuOpen, setMenuOpen] = useState(false);
  const streaming = message.status === "streaming";
  return (
    <article
      className={`chat-msg ${message.role === "author" ? "mine" : "theirs"}`}
      aria-label={message.role === "author" ? "作者消息" : "助手消息"}
    >
      <div className="chat-bubble">
        <ChatMarkdown text={message.text} />
        {streaming && <span className="chat-cursor" aria-hidden />}
      </div>
      {message.error && (
        <p className="chat-msg-error" role="alert">
          {message.error.why}（怎样算对：{message.error.fix}）
        </p>
      )}
      {message.status === "stopped" && (
        <p className="chat-msg-note">已停止；以上是已收到的部分回复。</p>
      )}
      {message.status === "failed" && !message.error && (
        <p className="chat-msg-note">回复失败；以上是已收到的部分回复。</p>
      )}
      <footer className="chat-msg-meta">
        <span>{message.model ?? "模型未记录"}</span>
        <ReferenceChips refs={message.refs} />
        {message.role === "assistant" && !streaming && (
          <span className="chat-msg-actions">
            {message.status === "failed" && message.turn_id && (
              <button
                className="text-blue underline"
                onClick={() => retry(message.turn_id!)}
              >
                重试这一轮
              </button>
            )}
            <button
              className="text-blue underline"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              准备为任务 ▾
            </button>
            <button
              className="text-blue underline"
              onClick={() => setDraft(message.text)}
            >
              以此为草稿
            </button>
          </span>
        )}
      </footer>
      {menuOpen && (
        <div className="chat-prepare-menu" role="group" aria-label="选择任务类型">
          {(Object.keys(taskKindLabels) as TaskKind[]).map((kind) => (
            <button
              key={kind}
              className="chat-prepare-item"
              onClick={() => {
                setMenuOpen(false);
                void draftFromMessage(message.message_id, kind);
              }}
            >
              {taskKindLabels[kind]}
            </button>
          ))}
          <p className="chat-prepare-note">
            只整理成任务建议；确认范围并点击「开始」后才创建创作任务。
          </p>
        </div>
      )}
    </article>
  );
}

export default function ChatMessages({
  onReviewCandidate,
  onOpenChange,
}: {
  onReviewCandidate(message: ChatMessage): void;
  onOpenChange(changeId: string): void;
}) {
  const messages = useChatStore(activeMessages);
  const turns = useChatStore(activeTurns);
  const abandoned = useChatStore(activeAbandoned);
  const activeTurn = useChatStore(activeTurnId);
  const pending = useChatStore((s) => activeView(s)?.pending ?? null);
  const retryUnknownSend = useChatStore((s) => s.retryUnknownSend);
  const adjustDraft = useChatStore((s) => s.adjustDraft);
  const startDraft = useChatStore((s) => s.startDraft);
  const abandonDraft = useChatStore((s) => s.abandonDraft);
  const acceptCandidate = useChatStore((s) => s.acceptCandidate);
  const cancelTask = useChatStore((s) => s.cancelTask);
  const retryTask = useChatStore((s) => s.retryTask);
  const boxRef = useRef<HTMLDivElement>(null);
  const [pinnedBottom, setPinnedBottom] = useState(true);
  const [newReply, setNewReply] = useState(false);
  const lastCount = useRef(0);
  const lastText = useRef("");

  const streamingText =
    messages.find((m) => m.status === "streaming")?.text ?? "";

  // 接近底部时跟随输出；用户上翻则不抢滚动位置。
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const nearBottom =
      box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    setPinnedBottom(nearBottom);
    if (nearBottom) box.scrollTop = box.scrollHeight;
  }, [messages, streamingText]);

  useEffect(() => {
    const grew =
      messages.length > lastCount.current ||
      (messages[ messages.length - 1 ]?.text ?? "").length > lastText.current.length;
    lastCount.current = messages.length;
    lastText.current = messages[messages.length - 1]?.text ?? "";
    if (grew && !pinnedBottom) setNewReply(true);
    if (pinnedBottom) setNewReply(false);
  }, [messages, pinnedBottom, streamingText]);

  return (
    <div className="chat-scroll" ref={boxRef} aria-label="对话消息">
      {pending && (
        <div className="chat-banner" role="alert">
          <span className="min-w-0 flex-1">{pending.error}</span>
          <button
            className="text-blue underline"
            onClick={() => void retryUnknownSend()}
          >
            用同一请求重试
          </button>
        </div>
      )}
      {messages.length === 0 && (
        <p className="chat-empty">
          还没有消息。普通发送只进行对话；明确点击创作操作才会创建任务。
        </p>
      )}
      {messages.map((m) => {
        if (m.kind === "text") return <TextMessage key={m.message_id} message={m} />;
        if (m.kind === "task_draft")
          return (
            <TaskDraftCard
              key={m.message_id}
              message={m}
              abandoned={abandoned[(m.payload as { draft_id: string }).draft_id] ?? false}
              onAdjust={(id, patch) => void adjustDraft(id, patch)}
              onStart={(id) => void startDraft(id)}
              onAbandon={(id) => void abandonDraft(id)}
            />
          );
        if (m.kind === "task_execution")
          return (
            <TaskExecutionCard
              key={m.message_id}
              message={m}
              onView={(candidateId) => {
                const node = document.getElementById(`chat-msg-${candidateId}`);
                node?.scrollIntoView({ behavior: "smooth", block: "center" });
              }}
              onCancel={(jobRef) => void cancelTask(jobRef)}
              onRetry={(jobRef) => void retryTask(jobRef)}
              onOpenChange={onOpenChange}
            />
          );
        if (m.kind === "candidate")
          return (
            <div key={m.message_id} id={`chat-msg-${m.message_id}`}>
              <CandidateCard
                message={m}
                onReview={() => onReviewCandidate(m)}
                onAccept={(id) => void acceptCandidate(id)}
              />
            </div>
          );
        return <ResultCard key={m.message_id} message={m} />;
      })}
      {activeTurn && turns[activeTurn]?.status === "queued" && (
        <p className="chat-msg-note" role="status">
          排队中…
        </p>
      )}
      {newReply && (
        <button
          className="chat-jump"
          onClick={() => {
            const box = boxRef.current;
            if (box) box.scrollTop = box.scrollHeight;
            setNewReply(false);
          }}
        >
          ↓ 有新回复
        </button>
      )}
    </div>
  );
}
