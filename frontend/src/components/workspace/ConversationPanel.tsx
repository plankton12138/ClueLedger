/**
 * 对话栏（0.4 V04-4 / V04-10）。
 *
 * 一次请求的完整过程：作者消息、工具调用、回复与该轮结果。结果卡把两条轴分开显示：
 * 执行状态（完成/部分/失败/已停止/中断）与记录状态（已记录/没有改动/历史未记录）。
 * 「文件已改但历史没记上」不能显示成全部成功。
 *
 * 会话状态来自 `state/conversations.ts`：每个会话各有事件与草稿，切换只看另一份，
 * 不共用「最后一个结果」——结果卡按轮次挂在它自己的位置后面。
 */

import { useState } from "react";

import type { Conversation, WorkspaceEvent } from "../../api/workspace";
import {
  draftKeyOf,
  runningConversation,
  selectedEntry,
  useConversationsStore,
} from "../../state/conversations";
import { useModelsStore } from "../../state/models";
import { useWorkspaceStore } from "../../state/workspace";
import ModelSelector from "../models/ModelSelector";

const STATUS_LABEL: Record<string, string> = {
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  stopped: "已停止",
  interrupted: "中断",
};

const RECORDING_LABEL: Record<string, string> = {
  committed: "已记录",
  no_change: "没有改动",
  unrecorded: "历史未记录",
};

const TOOL_LABEL: Record<string, string> = {
  list_files: "列出目录",
  read_file: "读取",
  search_text: "搜索",
  write_file: "写入",
  edit_file: "修改",
  move_file: "移动",
  delete_file: "删除",
  git_diff: "查看差异",
  git_log: "查看历史",
};

function toolLine(event: WorkspaceEvent): string {
  const name = TOOL_LABEL[event.tool ?? ""] ?? event.tool ?? "工具";
  const args = event.args ?? {};
  const path = typeof args.path === "string" ? args.path : "";
  const source = typeof args.source === "string" ? args.source : "";
  const target = typeof args.target === "string" ? args.target : "";
  const query = typeof args.query === "string" ? args.query : "";
  if (source && target) return `${name} ${source} → ${target}`;
  if (path) return `${name} ${path}`;
  if (query) return `${name}「${query}」`;
  return name;
}

function conversationLabel(item: Conversation, running: boolean): string {
  return running ? `${item.title}（进行中）` : item.title;
}

export default function ConversationPanel() {
  const items = useConversationsStore((state) => state.items);
  const selectedId = useConversationsStore((state) => state.selectedId);
  const entry = useConversationsStore(selectedEntry);
  const entries = useConversationsStore((state) => state.entries);
  const drafts = useConversationsStore((state) => state.drafts);
  const draft = useConversationsStore((state) => state.drafts[draftKeyOf(state)] ?? "");
  const pending = useConversationsStore((state) => state.pendingKey);
  const busyElsewhere = useConversationsStore(runningConversation);
  const error = useConversationsStore((state) => state.error);
  const select = useConversationsStore((state) => state.select);
  const startNew = useConversationsStore((state) => state.newConversation);
  const send = useConversationsStore((state) => state.send);
  const stop = useConversationsStore((state) => state.stop);
  const setDraft = useConversationsStore((state) => state.setDraft);
  const rename = useConversationsStore((state) => state.rename);
  const dismissError = useConversationsStore((state) => state.dismissError);
  const revert = useWorkspaceStore((state) => state.revert);
  const recordChanges = useWorkspaceStore((state) => state.recordChanges);

  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");

  // 未配置模型时给入口而不是拦死输入：草稿要保留，配好之后作者自己点发送。
  const selectionAvailable = useModelsStore((state) => state.selection.available);
  const openModelSettings = useModelsStore((state) => state.openSettings);

  const running = entry.running;
  const otherRunning = busyElsewhere !== null && busyElsewhere.id !== selectedId;
  const canSend = Boolean(draft.trim()) && !running && !pending && !otherRunning;

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
              {conversationLabel(item, Boolean(entries[item.id]?.running))}
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
          <button
            className="btn btn-gold px-2 py-0.5 text-[10px]"
            onClick={() => void commitRename()}
          >
            保存
          </button>
        </div>
      )}

      <div className="chat-bar flex flex-none items-center gap-2 px-3 py-1.5 text-[10px]">
        <span>模型</span>
        <ModelSelector />
      </div>

      <div className="chat-scroll">
        {entry.loading && <p className="text-[11px] text-mute">正在读取这段讨论…</p>}
        {!entry.loading && entry.events.length === 0 && (
          <p className="text-xs leading-6 text-sub">
            直接说要求，例如「写第一章，雨夜，侦探刚到旅馆」。普通讨论不会改动文件。
          </p>
        )}
        {entry.events.map((event) => {
          if (event.type === "turn_started") {
            return (
              <div key={event.seq} className="chat-msg mine">
                <div className="chat-bubble">{event.text}</div>
              </div>
            );
          }
          if (event.type === "assistant_message") {
            return (
              <div key={event.seq} className="chat-msg">
                <div className="chat-bubble">{event.text}</div>
              </div>
            );
          }
          if (event.type === "tool_started") {
            return (
              <div key={event.seq} className="chat-tool">
                · {toolLine(event)}…
              </div>
            );
          }
          if (event.type === "tool_finished") {
            return (
              <div key={event.seq} className={event.ok ? "chat-tool" : "chat-tool bad"}>
                {event.ok ? "✓" : "✗"} {toolLine(event)}
                {!event.ok && event.summary ? `：${event.summary}` : ""}
              </div>
            );
          }
          if (event.type === "error") {
            return (
              <div key={event.seq} className="chat-error">
                {event.message}
              </div>
            );
          }
          if (event.type === "run_finished") {
            const status = event.status ?? "completed";
            const recording = event.recording ?? "no_change";
            const paths = event.changed_paths ?? [];
            return (
              <div
                key={event.seq}
                className={`chat-card ${status === "completed" ? "result" : "warn"}`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-bold">{STATUS_LABEL[status] ?? status}</span>
                  <span className="text-sub">{RECORDING_LABEL[recording] ?? recording}</span>
                  {event.commit && (
                    <code className="text-[10px] text-mute">{event.commit.slice(0, 7)}</code>
                  )}
                </div>
                {paths.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-sub">
                    {paths.map((path) => (
                      <li key={path}>· {path}</li>
                    ))}
                  </ul>
                )}
                {event.note && <p className="mt-1 text-amber">{event.note}</p>}
                <div className="mt-2 flex gap-2">
                  {recording === "committed" && event.commit && (
                    <button
                      className="btn btn-gray px-2 py-0.5 text-[10px]"
                      onClick={() => void revert(event.commit!)}
                    >
                      撤销这次修改
                    </button>
                  )}
                  {recording === "unrecorded" && (
                    <button
                      className="btn btn-gray px-2 py-0.5 text-[10px]"
                      onClick={() => void recordChanges()}
                    >
                      补记到历史
                    </button>
                  )}
                </div>
              </div>
            );
          }
          return null;
        })}

        {entry.error && <div className="chat-error">{entry.error}</div>}
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
    </aside>
  );
}
