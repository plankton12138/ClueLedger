/**
 * 对话栏（0.4 V04-4 / V04-10 / V05-2）。
 *
 * 这里只负责装配：会话选择、模型、草稿、发送与停止，以及把每轮交给 `ChatTurnView`。
 * 消息怎么归并、过程怎么折叠、回复怎么复制与展开，都在 `components/chat/` 与
 * `state/chatMessages.ts` 里，本组件不解析事件。
 *
 * 结果卡把两条轴分开：执行状态（完成/部分/失败/已停止/中断）与记录状态
 * （已记录/没有改动/历史未记录）。「文件已改但历史没记上」不能显示成全部成功。
 */

import { useCallback, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import type { Conversation } from "../../api/workspace";
import { findMessage, type ChatMessage } from "../../state/chatMessages";
import {
  draftKeyOf,
  runningConversation,
  selectedEntry,
  useConversationsStore,
} from "../../state/conversations";
import { useModelsStore } from "../../state/models";
import { useWorkspaceStore } from "../../state/workspace";
import ModelSelector from "../models/ModelSelector";
import ChatTurnView from "../chat/ChatTurnView";
import ExpandedReplyDialog from "../chat/ExpandedReplyDialog";
import { useChatScroll } from "../chat/useChatScroll";

function conversationLabel(item: Conversation, running: boolean): string {
  return running ? `${item.title}（进行中）` : item.title;
}

export default function ConversationPanel() {
  const items = useConversationsStore((state) => state.items);
  const selectedId = useConversationsStore((state) => state.selectedId);
  const entry = useConversationsStore(selectedEntry);
  const runningIds = useConversationsStore(useShallow((state) =>
    Object.keys(state.entries).filter((id) => state.entries[id]?.running),
  ));
  const drafts = useConversationsStore((state) => state.drafts);
  const draft = useConversationsStore((state) => state.drafts[draftKeyOf(state)] ?? "");
  const pending = useConversationsStore((state) => state.pendingKey);
  const busyElsewhere = useConversationsStore(runningConversation);
  const error = useConversationsStore((state) => state.error);
  const select = useConversationsStore((state) => state.select);
  const startNew = useConversationsStore((state) => state.newConversation);
  const send = useConversationsStore((state) => state.send);
  const stop = useConversationsStore((state) => state.stop);
  const reconnect = useConversationsStore((state) => state.reconnect);
  const setDraft = useConversationsStore((state) => state.setDraft);
  const rename = useConversationsStore((state) => state.rename);
  const dismissError = useConversationsStore((state) => state.dismissError);
  const revert = useWorkspaceStore((state) => state.revert);
  const recordChanges = useWorkspaceStore((state) => state.recordChanges);

  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  // 宽阅读视图绑定「哪个会话的哪条回复」：切走就关闭，不显示错会话的内容。
  const [expanded, setExpanded] = useState<{ conversationId: string; messageId: string } | null>(
    null,
  );
  const [copyHint, setCopyHint] = useState(false);
  const scroll = useChatScroll(entry.lastSeq, selectedId);

  // 未配置模型时给入口而不是拦死输入：草稿要保留，配好之后作者自己点发送。
  const selectionAvailable = useModelsStore((state) => state.selection.available);
  const openModelSettings = useModelsStore((state) => state.openSettings);

  const running = entry.running;
  const otherRunning = busyElsewhere !== null && busyElsewhere.id !== selectedId;
  const canSend = Boolean(draft.trim()) && !running && !pending && !otherRunning;

  const expandedMessage: ChatMessage | null = useMemo(() => {
    if (!expanded || expanded.conversationId !== selectedId) return null;
    return findMessage(entry.turns, expanded.messageId);
  }, [expanded, selectedId, entry.turns]);

  const openExpanded = useCallback((message: ChatMessage) => {
    if (!selectedId) return;
    setCopyHint(false);
    setExpanded({ conversationId: selectedId, messageId: message.id });
  }, [selectedId]);

  const copyFailed = useCallback((message: ChatMessage) => {
    // 剪贴板失败：把同一条回复打开成可选择复制的原文，不假装复制成功。
    if (!selectedId) return;
    setCopyHint(true);
    setExpanded({ conversationId: selectedId, messageId: message.id });
  }, [selectedId]);

  const onRevert = useCallback((commit: string) => void revert(commit), [revert]);
  const onRecord = useCallback(() => void recordChanges(), [recordChanges]);

  const submit = async () => {
    if (!canSend) return;
    await send(draft);
  };

  const commitRename = async () => {
    if (selectedId) await rename(selectedId, titleDraft);
    setRenaming(false);
  };

  return (
    <aside className="chat-shell h-full">
      <div className="chat-head flex flex-none items-center gap-2 px-3 py-2">
        <select
          aria-label="选择对话"
          className="well-input min-w-0 flex-1 px-2 py-1 text-xs"
          value={selectedId ?? ""}
          onChange={(event) => {
            if (event.target.value) void select(event.target.value);
          }}
        >
          <option value="">{drafts.new ? "（新对话 · 草稿）" : "（新对话）"}</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {conversationLabel(item, runningIds.includes(item.id))}
            </option>
          ))}
        </select>
        <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={startNew}>
          新对话
        </button>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={!selectedId}
          onClick={() => {
            const current = items.find((item) => item.id === selectedId);
            setTitleDraft(current?.title ?? "");
            setRenaming(true);
          }}
        >
          改名
        </button>
      </div>

      {renaming && selectedId && (
        <div className="flex flex-none items-center gap-2 border-b border-panel-edge px-3 py-1.5">
          <input
            aria-label="会话标题"
            className="well-input min-w-0 flex-1 px-2 py-1 text-xs"
            value={titleDraft}
            onChange={(event) => setTitleDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void commitRename();
              if (event.key === "Escape") setRenaming(false);
            }}
          />
          <button className="btn btn-gold px-2 py-0.5 text-[10px]" onClick={() => void commitRename()}>
            保存
          </button>
        </div>
      )}

      <div className="chat-bar flex flex-none items-center gap-2 px-3 py-1.5 text-[10px]">
        <span>模型</span>
        <ModelSelector />
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scroll.ref}
          className="chat-scroll"
          onScroll={scroll.onScroll}
          role="log"
          aria-label="讨论内容"
        >
          {entry.loading && <p className="text-[11px] text-mute">正在读取这段讨论…</p>}
          {!entry.loading && entry.turns.length === 0 && (
            <p className="text-xs leading-6 text-sub">
              直接说要求，例如「写第一章，雨夜，侦探刚到旅馆」。普通讨论不会改动文件。
            </p>
          )}
          {entry.turns.map((turn, index) => (
            <ChatTurnView
              key={turn.id}
              turn={turn}
              running={running && index === entry.turns.length - 1}
              phase={running && index === entry.turns.length - 1 ? entry.phase : null}
              onExpand={openExpanded}
              onCopyFailed={copyFailed}
              onRevert={onRevert}
              onRecord={onRecord}
            />
          ))}

          {entry.error && (
            <div className="chat-error flex items-start gap-2">
              <span className="min-w-0 flex-1">{entry.error}</span>
              {running && (
                <button className="font-bold" onClick={() => void reconnect()}>
                  重新连接
                </button>
              )}
            </div>
          )}
        </div>

        {scroll.hasNew && (
          <button type="button" className="chat-jump" onClick={scroll.toLatest}>
            回到最新
          </button>
        )}
      </div>

      <div className="chat-composer">
        {error && (
          <div className="chat-error flex items-start gap-2">
            <span className="min-w-0 flex-1">{error}</span>
            <button className="font-bold" onClick={dismissError}>
              关闭
            </button>
          </div>
        )}
        {!selectionAvailable && (
          <div className="flex items-center gap-2 rounded border border-blue-edge bg-blue-bg px-2 py-1.5 text-[11px] text-blue">
            <span className="min-w-0 flex-1">连接模型后开始对话。</span>
            <button className="btn btn-gold px-2 py-0.5 text-[10px]" onClick={openModelSettings}>
              去设置
            </button>
          </div>
        )}
        {otherRunning && busyElsewhere && (
          <div className="rounded border border-amber-edge bg-amber-bg px-2 py-1.5 text-[11px] text-amber">
            「{busyElsewhere.title}」还在进行；结束或停止它之后才能发送。
          </div>
        )}
        <textarea
          className="chat-input"
          placeholder={
            running
              ? "这一轮还在进行…"
              : otherRunning
                ? "另一个会话还在进行…"
                : "说要求，回车发送，Shift+Enter 换行"
          }
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          {running ? (
            <button className="btn btn-gray px-3 py-1 text-[11px]" onClick={() => void stop()}>
              停止
            </button>
          ) : (
            <button
              className="btn btn-gold px-3 py-1 text-[11px]"
              disabled={!canSend}
              onClick={() => void submit()}
            >
              发送
            </button>
          )}
        </div>
      </div>

      <ExpandedReplyDialog
        message={expandedMessage}
        open={Boolean(expandedMessage)}
        copyHint={copyHint}
        onClose={() => setExpanded(null)}
        onCopyFailed={() => setCopyHint(true)}
      />
    </aside>
  );
}
