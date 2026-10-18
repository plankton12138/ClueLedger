/**
 * 写作台唯一输入框（M01/V03-1.1 接线）。
 *
 * 当前走 V03-1.2 无作品入口（save_idea）：想法落入口对应的构思作品，
 * 不启动模型；「接着讨论」与向当前作品发消息随 V03-1.5 接线。
 * 幂等：entry_id 浏览器内稳定；request_id 首次提交生成、失败重试复用、
 * 成功清除（api/entries.ts）。草稿刷新后从 localStorage 恢复。
 */

import { useEffect, useRef, useState } from "react";
import {
  clearPendingRequestId,
  ideaTitle,
  newRequestId,
  readIdeaDraft,
  readPendingRequestId,
  readStableEntryId,
  saveIdea,
  writeIdeaDraft,
  writePendingRequestId,
} from "../../api/entries";
import { useChatStore } from "../../state/chat";
import { useDeskStore } from "../../state/deskStore";
import { appendReference } from "../../state/graphLayout";
import { message, registerProject, useV2Store } from "../../state/v2";
import { ErrorText } from "../Workbench";

const KIND_LABELS: Record<string, string> = {
  api: "模型已连接",
  simulator: "模拟模式（未连接真实服务）",
  probing: "正在检测模型服务…",
  unsupported: "未连接模型服务",
};

export default function DeskComposer({
  onOpenModels,
}: {
  onOpenModels?: () => void;
}) {
  const [draft, setDraft] = useState(() => readIdeaDraft());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const serviceKind = useChatStore((st) => st.serviceKind);
  const projects = useV2Store((s) => s.projects);
  const composerFocusTick = useDeskStore((s) => s.composerFocusTick);
  const composerInsert = useDeskStore((s) => s.composerInsert);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 接着讨论成功后恢复原输入位置（焦点回到唯一输入框，M02）。
  useEffect(() => {
    if (composerFocusTick > 0) inputRef.current?.focus();
  }, [composerFocusTick]);

  // 「加入讨论」（V03-6.4）：把图谱选中对象作为可见引用追加到草稿并聚焦。
  // 草稿仍是本地文字，未保存前不产生任何领域写入。
  const draftRef = useRef(draft);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  useEffect(() => {
    if (!composerInsert) return;
    const next = appendReference(draftRef.current, composerInsert.text);
    setDraft(next);
    writeIdeaDraft(next);
    inputRef.current?.focus();
  }, [composerInsert]);

  function onChange(text: string) {
    setDraft(text);
    writeIdeaDraft(text);
  }

  async function submit() {
    const text = draft.trim();
    if (text === "" || busy) return;
    // 失败重试复用同一 request_id（服务端按 request_id+文本哈希去重）。
    const existing = readPendingRequestId(text);
    const requestId = existing ?? newRequestId();
    if (existing === null) writePendingRequestId(text, requestId);
    setBusy(true);
    setError(null);
    try {
      const saved = await saveIdea({
        entryId: readStableEntryId(),
        requestId,
        text,
        title: ideaTitle(text),
      });
      clearPendingRequestId(text);
      useDeskStore.getState().addEntryReceipt({
        entryId: saved.entry_id,
        projectId: saved.project_id,
        conversationId: saved.conversation_id,
        messageId: saved.message_id,
        text,
        status: saved.status,
        savedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      });
      onChange("");
      // 想法落成的作品进入顶部选择器（已登记则保留原显示名）。
      if (!projects.some((p) => p.projectId === saved.project_id)) {
        registerProject({
          projectId: saved.project_id,
          name: ideaTitle(text),
          source: "created",
        });
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-t border-line bg-white/70 px-4 py-3">
      {serviceKind !== "api" && (
        <p className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-sub">
          <span>{KIND_LABELS[serviceKind] ?? "模型状态未知"}</span>
          <span>想法会先保存为文字，连接模型后再继续讨论。</span>
          {onOpenModels && (
            <button className="text-blue underline" onClick={onOpenModels}>
              打开模型设置
            </button>
          )}
        </p>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={inputRef}
          aria-label="想法输入框"
          className="well-input min-h-[52px] max-h-40 flex-1 resize-y p-2 text-xs leading-6"
          placeholder="记下构思、人物、场景…保存为文字，不调用模型。"
          value={draft}
          disabled={busy}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <button
          className="btn shrink-0 px-4 py-2 text-xs"
          disabled={busy || draft.trim() === ""}
          onClick={() => void submit()}
        >
          {busy ? "保存中…" : "保存想法"}
        </button>
      </div>
      <p className="mt-1 text-[10px] text-sub">
        Ctrl+Enter 保存 · 失败重试不会重复创建作品
      </p>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
