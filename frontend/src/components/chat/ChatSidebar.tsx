/**
 * 常驻对话侧栏（§3.1）：七页共用，切页不销毁会话。
 *
 * - 桌面默认约 380px，可在合理范围拖动；可收起，收起后顶栏按钮重新打开；
 * - 窄屏（<lg）经顶栏按钮以覆盖面板打开，关闭后返回原页面与滚动位置；
 * - 收起、切页、切会话不停止后台响应；显示未读/活动状态；
 * - 模拟模式常驻标识；无对话服务（旧后端）时给明确的重启/能力指引，
   不展示空白或虚假成功。
 */

import { useEffect, useState } from "react";
import { MessageCircle, MessageSquarePlus, PanelRightClose, X } from "lucide-react";
import { activeConversations, activeView, simulator, useChatStore } from "../../state/chat";
import { useV2Store } from "../../state/v2";
import ChatMessages from "./ChatMessages";
import ChatComposer from "./ChatComposer";

const MIN_W = 320;
const MAX_W = 560;
const DEFAULT_W = 380;
const WIDTH_KEY = "hastings.chat.width";
const OPEN_KEY = "hastings.chat.open";

function readWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_KEY);
    const value = raw === null ? DEFAULT_W : Number(raw);
    if (!Number.isFinite(value)) return DEFAULT_W;
    return Math.min(MAX_W, Math.max(MIN_W, value));
  } catch {
    return DEFAULT_W;
  }
}

export default function ChatSidebar({
  open,
  narrow,
  onClose,
  onReviewCandidate,
}: {
  open: boolean;
  narrow: boolean;
  onClose(): void;
  onReviewCandidate(): void;
}) {
  const projectId = useV2Store((s) => s.currentId);
  const bindProject = useChatStore((s) => s.bindProject);
  const conversations = useChatStore(activeConversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const reloadMessages = useChatStore((s) => s.reloadMessages);
  const newConversation = useChatStore((s) => s.newConversation);
  const service = useChatStore((s) => s.service);
  const serviceKind = useChatStore((s) => s.serviceKind);
  const capabilityDetail = useChatStore((s) => s.capabilityDetail);
  const loadError = useChatStore((s) => s.loadError);
  const view = useChatStore(activeView);
  const error = view?.error ?? null;
  const clearError = useChatStore((s) => s.clearError);
  const loading = view?.loading ?? false;
  const connection = view?.connection ?? "closed";
  const [width, setWidth] = useState(readWidth);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [resizing, setResizing] = useState(false);

  // 项目切换重新绑定会话；草稿与引用按会话保留。
  useEffect(() => {
    void bindProject(projectId);
  }, [bindProject, projectId]);

  // 模拟器只读快照：当前章 / 已接受节拍 / 当前模型（不写入任何项目内容）。
  const chapter = useV2Store((s) => s.chapter);
  const beats = useV2Store((s) => s.storyArtifacts);
  useEffect(() => {
    if (!service || service.kind !== "simulator") return;
    simulator.setView({
      hasAcceptedBeats: beats.some((a) => a.kind === "narrative.beat"),
      currentChapter: chapter.chapterId
        ? {
            chapter_id: chapter.chapterId,
            title: chapter.title,
            base: chapter.chapterBase,
          }
        : null,
    });
  }, [service, chapter.chapterId, chapter.title, chapter.chapterBase, beats]);

  useEffect(() => {
    if (!resizing) return;
    const move = (e: MouseEvent) => {
      setWidth(Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - e.clientX)));
    };
    const up = () => {
      setResizing(false);
      try {
        localStorage.setItem(WIDTH_KEY, String(width));
      } catch {
        /* 禁用存储时仅本次会话生效 */
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [resizing, width]);

  if (!open) return null;

  const active = conversations.find((c) => c.conversation_id === activeConversationId);
  const simulated = service?.capabilities().simulated ?? false;

  const body = (
    <>
      <header className="chat-head">
        <div className="min-w-0 flex-1">
          <div className="relative">
            <button
              className="chat-title"
              aria-expanded={historyOpen}
              onClick={() => setHistoryOpen((v) => !v)}
            >
              <span className="truncate">{active?.title ?? "项目对话"}</span>
              <span className="chat-title-caret">▾</span>
            </button>
            {historyOpen && (
              <div className="chat-menu chat-history" role="listbox" aria-label="会话历史">
                {conversations.map((c) => (
                  <button
                    key={c.conversation_id}
                    className={`chat-menu-item ${c.conversation_id === activeConversationId ? "on" : ""}`}
                    role="option"
                    aria-selected={c.conversation_id === activeConversationId}
                    onClick={() => {
                      setHistoryOpen(false);
                      void selectConversation(c.conversation_id);
                    }}
                  >
                    {c.title}
                  </button>
                ))}
                {conversations.length === 0 && (
                  <p className="chat-menu-empty">还没有会话。</p>
                )}
              </div>
            )}
          </div>
          <p className="chat-subtitle">
            {simulated ? "模拟模式 · 不连接真实模型" : "项目内对话"}
          </p>
        </div>
        <button
          className="chat-icon-btn"
          aria-label="新建会话"
          title="新建会话（不携带其他会话的草稿与引用）"
          onClick={() => void newConversation()}
        >
          <MessageSquarePlus size={15} />
        </button>
        <button
          className="chat-icon-btn"
          aria-label={narrow ? "关闭对话面板" : "收起对话栏"}
          onClick={onClose}
        >
          {narrow ? <X size={15} /> : <PanelRightClose size={15} />}
        </button>
      </header>
      {!projectId ? (
        <div className="chat-guide">
          <p>还没有选择项目。第一版对话归属于项目。</p>
          <p className="chat-guide-note">
            先在顶栏选择或新建项目，再开始对话；聊天不会创建仓库提交。
          </p>
        </div>
      ) : serviceKind === "unsupported" && !service ? (
        <div className="chat-guide" role="alert">
          <p>当前后端不提供对话服务。</p>
          <p className="chat-guide-note">
            {capabilityDetail ||
              "这是前后端版本不匹配：重启后端（新版包含 /api/v2 对话端点）后刷新页面。"}
            在旧后端下不会显示任何虚假对话内容。
          </p>
        </div>
      ) : serviceKind === "probing" ? (
        <p className="chat-loading" role="status">
          正在检查对话服务…
        </p>
      ) : (
        <>
          {loadError && (
            <div className="chat-error" role="alert">
              <span className="min-w-0 flex-1">{loadError}</span>
              <button
                className="text-blue underline"
                onClick={() => void bindProject(projectId)}
              >
                重试
              </button>
            </div>
          )}
          {error && (
            <div className="chat-error" role="alert">
              <span className="min-w-0 flex-1">{error}</span>
              <button className="chat-error-close" aria-label="关闭错误提示" onClick={clearError}>
                ×
              </button>
            </div>
          )}
          {loading && (
            <p className="chat-loading" role="status">
              正在读取会话…
            </p>
          )}
          {connection === "closed" && !loading && (
            <div className="chat-banner" role="status">
              <span className="min-w-0 flex-1">
                对话连接已断开，正在重连；已收到的内容仍在。
              </span>
              <button
                className="text-blue underline"
                onClick={() => void reloadMessages()}
              >
                重新读取
              </button>
            </div>
          )}
          <ChatMessages onReviewCandidate={onReviewCandidate} />
          <ChatComposer />
        </>
      )}
    </>
  );

  if (narrow) {
    return (
      <aside className="chat-overlay" aria-label="对话面板">
        <div className="chat-shell chat-shell-overlay">{body}</div>
      </aside>
    );
  }

  return (
    <aside
      className="chat-shell"
      aria-label="对话栏"
      style={{ width }}
      data-testid="chat-sidebar"
    >
      <div
        className="chat-resize"
        role="separator"
        aria-label="调整对话栏宽度"
        onMouseDown={() => setResizing(true)}
      />
      {body}
    </aside>
  );
}

/** 顶栏对话栏开关：桌面收起/展开，窄屏打开覆盖面板。 */
export function ChatToggle({
  open,
  unread,
  onToggle,
}: {
  open: boolean;
  unread: number;
  onToggle(): void;
}) {
  return (
    <button
      className="project-picker relative grid h-[34px] w-[34px] shrink-0 place-items-center"
      aria-label={open ? "隐藏对话栏" : "显示对话栏"}
      onClick={onToggle}
    >
      <MessageCircle size={16} />
      {!open && unread > 0 && (
        <span className="nav-badge absolute -right-1 -top-1 px-1 text-[9px]">
          {unread}
        </span>
      )}
    </button>
  );
}

export { MIN_W, MAX_W, DEFAULT_W, OPEN_KEY };
