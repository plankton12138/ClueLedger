/**
 * 输入框（§3.2）：普通「发送」固定为聊天；创作操作是显式入口。
 *
 * - Enter 发送、Shift+Enter 换行；中文输入法组合输入期间（composition）不发送；
 * - 流式期间显示「停止回复」，下一条草稿仍可编辑；同会话不并发两轮；
 * - 已附加引用可删除、可展开；切页不替换引用；
 * - 模型标签来自服务端当前选择；消息记录本轮实际模型；
 * - 创作操作（规划/起草/检查/定向修订）进入同一 TaskDraft，不直接启动。
 */

import { useEffect, useRef, useState } from "react";
import { Paperclip, Send, Square } from "lucide-react";
import { activeDraft, activeRefs, activeTurnId, useChatStore } from "../../state/chat";
import { useV2Store } from "../../state/v2";
import { useModelStore } from "../../state/models";
import {
  artifactRef,
  chapterRef,
  onSelectionOffer,
  selectionRef,
  type SelectionOffer,
} from "./refs";
import { titleOf } from "../Workbench";
import type { ChatReference, TaskKind } from "../../api/chat";
import { taskKindLabels } from "../../api/chat";

const creationActions: { kind: TaskKind; hint: string }[] = [
  { kind: "plan", hint: "产出故事前提与节拍候选，先审阅再接受" },
  { kind: "chapter_draft", hint: "基于已接受节拍生成正文候选" },
  { kind: "check", hint: "检查人物知情与线索公平性，不自动修复" },
  { kind: "targeted_revision", hint: "按设定变化走影响分析与批准流程" },
];


export default function ChatComposer() {
  const draft = useChatStore(activeDraft);
  const setDraft = useChatStore((s) => s.setDraft);
  const refs = useChatStore(activeRefs);
  const addRef = useChatStore((s) => s.addRef);
  const removeRef = useChatStore((s) => s.removeRef);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const createDraft = useChatStore((s) => s.createDraft);
  const activeTurn = useChatStore(activeTurnId);
  const service = useChatStore((s) => s.service);
  const chapter = useV2Store((s) => s.chapter);
  const storyArtifacts = useV2Store((s) => s.storyArtifacts);
  const model = useModelStore((s) => s.current);
  const composing = useRef(false);
  const [refMenuOpen, setRefMenuOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [selection, setSelection] = useState<SelectionOffer | null>(null);
  const [refsOpen, setRefsOpen] = useState(true);

  useEffect(() => onSelectionOffer(setSelection), []);

  const modelLabel = service
    ? (model?.model ?? service.currentModel() ?? "模拟模型")
    : "对话服务未连接";

  function attach(ref: ChatReference) {
    addRef(ref);
    setRefMenuOpen(false);
  }

  return (
    <div className="chat-composer">
      {refs.length > 0 && (
        <div className="chat-refs-bar">
          <button
            className="chat-refs-toggle"
            aria-expanded={refsOpen}
            onClick={() => setRefsOpen((v) => !v)}
          >
            <Paperclip size={12} /> 已附加 {refs.length} 条上下文
          </button>
          {refsOpen && (
            <ul aria-label="已附加上下文">
              {refs.map((r) => (
                <li key={r.ref_id}>
                  <span className={`chat-ref-chip ${r.saved ? "" : "unsaved"}`}>
                    {r.title}
                    {r.saved && r.version ? ` · ${r.version.slice(0, 8)}` : " · 未保存摘录"}
                  </span>
                  <button
                    className="chat-ref-remove"
                    aria-label={`移除引用 ${r.title}`}
                    onClick={() => removeRef(r.ref_id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {createOpen && (
        <div className="chat-create" role="group" aria-label="创作操作">
          <p className="chat-create-note">
            创作操作只整理任务建议；点击「开始」后才创建创作任务。
          </p>
          {creationActions.map((action) => (
            <button
              key={action.kind}
              className="chat-create-item"
              onClick={() => {
                setCreateOpen(false);
                void createDraft(action.kind);
              }}
            >
              <b>{taskKindLabels[action.kind]}</b>
              <span>{action.hint}</span>
            </button>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <textarea
          className="chat-input"
          aria-label="对话输入"
          placeholder={
            activeTurn ? "回复中，可继续编辑下一条…" : "讨论构想；Enter 发送，Shift+Enter 换行"
          }
          value={draft}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onCompositionStart={() => (composing.current = true)}
          onCompositionEnd={() => (composing.current = false)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !composing.current && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <div className="chat-input-buttons">
          {activeTurn ? (
            <button
              className="btn btn-gray chat-send"
              aria-label="停止回复"
              onClick={() => void stop()}
            >
              <Square size={13} /> 停止回复
            </button>
          ) : (
            <button
              className="btn chat-send"
              aria-label="发送"
              disabled={!service || draft.trim() === ""}
              onClick={() => void send()}
            >
              <Send size={13} /> 发送
            </button>
          )}
        </div>
      </div>
      <div className="chat-input-actions">
        <span className="chat-model" title="发送前显示服务端当前选择；切换影响后续发送">
          模型：{modelLabel}
        </span>
        <div className="relative">
          <button
            className="chat-mini-btn"
            aria-expanded={refMenuOpen}
            onClick={() => setRefMenuOpen((v) => !v)}
          >
            ＋ 引用
          </button>
          {refMenuOpen && (
            <div className="chat-menu" role="group" aria-label="添加上下文引用">
              <button
                className="chat-menu-item"
                disabled={!chapter.chapterId}
                onClick={() =>
                  chapter.chapterId &&
                  attach(
                    chapterRef({
                      chapter_id: chapter.chapterId,
                      title: chapter.title,
                      chapter_base: chapter.chapterBase,
                    }),
                  )
                }
              >
                当前章{chapter.title ? ` · ${chapter.title}` : ""}
              </button>
              <button
                className="chat-menu-item"
                disabled={!selection}
                onClick={() => selection && attach(selectionRef(selection))}
              >
                选中文本{selection ? "（可附加）" : "（在创作台选中后可用）"}
              </button>
              <div className="chat-menu-head">故事对象</div>
              <div className="chat-menu-scroll">
                {storyArtifacts.slice(0, 40).map((a) => (
                  <button
                    key={a.artifact_id}
                    className="chat-menu-item"
                    onClick={() => attach(artifactRef(a))}
                  >
                    {titleOf(a)}
                  </button>
                ))}
                {storyArtifacts.length === 0 && (
                  <p className="chat-menu-empty">项目还没有故事对象。</p>
                )}
              </div>
            </div>
          )}
        </div>
        <button
          className="chat-mini-btn"
          aria-expanded={createOpen}
          onClick={() => setCreateOpen((v) => !v)}
        >
          ✎ 创作操作
        </button>
      </div>
    </div>
  );
}
