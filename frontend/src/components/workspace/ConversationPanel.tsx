/**
 * 对话栏（0.4 V04-4）：一轮请求的完整过程——作者消息、工具调用、回复与结果。
 *
 * 结果卡把两条轴分开显示：执行状态（完成/部分/失败/已停止/中断）与记录状态
 * （已记录/没有改动/历史未记录）。「文件已改但历史没记上」不能显示成全部成功。
 */

import { useState } from "react";

import type { WorkspaceEvent } from "../../api/workspace";
import { useWorkspaceStore } from "../../state/workspace";

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

export default function ConversationPanel() {
  const events = useWorkspaceStore((state) => state.events);
  const running = useWorkspaceStore((state) => state.running);
  const lastResult = useWorkspaceStore((state) => state.lastResult);
  const conversations = useWorkspaceStore((state) => state.conversations);
  const conversationId = useWorkspaceStore((state) => state.conversationId);
  const send = useWorkspaceStore((state) => state.send);
  const stop = useWorkspaceStore((state) => state.stop);
  const selectConversation = useWorkspaceStore((state) => state.selectConversation);
  const newConversation = useWorkspaceStore((state) => state.newConversation);
  const revert = useWorkspaceStore((state) => state.revert);
  const recordChanges = useWorkspaceStore((state) => state.recordChanges);
  const [draft, setDraft] = useState("");

  const submit = async () => {
    if (!draft.trim() || running) return;
    const text = draft;
    setDraft("");
    await send(text);
  };

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-[#355f7f] bg-[#12354f] text-white">
      <div className="flex flex-none items-center gap-2 border-b border-[#355f7f] px-3 py-2">
        <select
          aria-label="选择对话"
          className="min-w-0 flex-1 rounded bg-[#1b4666] px-2 py-1 text-xs text-white"
          value={conversationId ?? ""}
          onChange={(event) => {
            if (event.target.value) void selectConversation(event.target.value);
          }}
        >
          <option value="">（新对话）</option>
          {conversations.map((conversation) => (
            <option key={conversation.id} value={conversation.id}>
              {conversation.title}
            </option>
          ))}
        </select>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          onClick={() => void newConversation()}
        >
          新对话
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-3 py-3">
        {events.length === 0 && (
          <p className="text-xs leading-6 text-white/60">
            直接说要求，例如「写第一章，雨夜，侦探刚到旅馆」。普通讨论不会改动文件。
          </p>
        )}
        {events.map((event) => {
          if (event.type === "turn_started") {
            return (
              <div key={event.seq} className="ml-6 rounded bg-[#1b4666] px-3 py-2 text-xs leading-6">
                {event.text}
              </div>
            );
          }
          if (event.type === "assistant_message") {
            return (
              <div key={event.seq} className="mr-2 whitespace-pre-wrap text-xs leading-6 text-white/90">
                {event.text}
              </div>
            );
          }
          if (event.type === "tool_started") {
            return (
              <div key={event.seq} className="text-[11px] text-white/45">
                · {toolLine(event)}…
              </div>
            );
          }
          if (event.type === "tool_finished") {
            return (
              <div
                key={event.seq}
                className={`text-[11px] ${event.ok ? "text-[#9fd2a8]" : "text-[#f0a8a8]"}`}
              >
                {event.ok ? "✓" : "✗"} {toolLine(event)}
                {!event.ok && event.summary ? `：${event.summary}` : ""}
              </div>
            );
          }
          if (event.type === "error") {
            return (
              <div key={event.seq} className="rounded bg-[#5a2230] px-3 py-2 text-[11px] leading-5">
                {event.message}
              </div>
            );
          }
          return null;
        })}

        {lastResult && (
          <div className="rounded border border-[#355f7f] bg-[#0e2b41] px-3 py-2 text-[11px] leading-5">
            <div className="flex items-center gap-2">
              <span className="font-bold">{STATUS_LABEL[lastResult.status] ?? lastResult.status}</span>
              <span className="text-white/50">
                {RECORDING_LABEL[lastResult.recording] ?? lastResult.recording}
              </span>
              {lastResult.commit && (
                <code className="text-[10px] text-white/45">{lastResult.commit.slice(0, 7)}</code>
              )}
            </div>
            {lastResult.changed_paths.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-white/70">
                {lastResult.changed_paths.map((path) => (
                  <li key={path}>· {path}</li>
                ))}
              </ul>
            )}
            {lastResult.note && <p className="mt-1 text-[#f0c48a]">{lastResult.note}</p>}
            <div className="mt-2 flex gap-2">
              {lastResult.recording === "committed" && lastResult.commit && (
                <button
                  className="btn btn-gray px-2 py-0.5 text-[10px]"
                  onClick={() => void revert(lastResult.commit!)}
                >
                  撤销这次修改
                </button>
              )}
              {lastResult.recording === "unrecorded" && (
                <button
                  className="btn btn-gray px-2 py-0.5 text-[10px]"
                  onClick={() => void recordChanges()}
                >
                  补记到历史
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="flex-none border-t border-[#355f7f] p-2">
        <textarea
          className="h-20 w-full resize-none rounded bg-[#0e2b41] px-3 py-2 text-xs leading-5 text-white placeholder:text-white/35"
          placeholder={running ? "这一轮还在进行…" : "说要求，回车发送，Shift+Enter 换行"}
          value={draft}
          disabled={running}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="mt-2 flex justify-end gap-2">
          {running ? (
            <button className="btn btn-gray px-3 py-1 text-[11px]" onClick={() => void stop()}>
              停止
            </button>
          ) : (
            <button
              className="btn btn-gold px-3 py-1 text-[11px]"
              disabled={!draft.trim()}
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
