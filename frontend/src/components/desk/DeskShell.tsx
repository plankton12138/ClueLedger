/**
 * 写作台壳（V03-10 收拢中）：左栏（章节目录 + 对话历史）、中区稿纸、右栏标记。
 *
 * V03-10 M1：删「写想法 → 接着讨论」两步入口，对话区加自由输入框——回车即一轮，
 * 创作与设定改动都经对话内「采用」落盘。左栏「想法与讨论」移出，改为可切换的
 * 「对话历史」会话列表。
 * V03-10 M4：撤下高级右栏入口（关联图谱、整体调整、后续状态、操作结果、证据核对、
 * 版本历史），右栏标记只剩「设定集」一枚；图谱与后续状态面板及其读接口一并移除。
 *
 * 不变量全部消费 state/desk.ts 纯函数，不在组件里重写：
 * - 主区一次只展开一个（MainView），navigate 是唯一变更入口；
 * - 初始焦点 selectInitialChapterId（有正文恢复上次章节，无记录取第一章，
 *   无正文按构思状态）；
 * - 阅读位置四元组（project|chapter|formal|-），滚动保存、打开恢复。
 *
 * 响应式（M14 / V03-8.5 走查）：宽屏（≥1280）三部分；中屏（1024–1279）折叠
 * 左导航为抽屉、保留右栏；小屏（<1024）主区优先，左右都走抽屉。断点以可读性
 * 确定，不把三列按比例缩小。临时视图只列已有真实视图的入口（无伪入口）。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage } from "../../api/chat";
import { fetchManuscript } from "../../api/v2";
import { activeConversations, simulator, useChatStore } from "../../state/chat";
import {
  selectInitialChapterId,
  type MainView,
  type SourceKind,
} from "../../state/desk";
import { useDeskStore } from "../../state/deskStore";
import { useV2Store } from "../../state/v2";
import { Empty, ErrorText } from "../Workbench";
import {
  CandidateCard,
  ResultCard,
  TaskDraftCard,
  TaskExecutionCard,
} from "../chat/ChatCards";
import ChatCandidateReview from "../chat/ChatCandidateReview";
import ImportDialog from "./ImportDialog";
import LedgerPanel from "./LedgerPanel";

/** 右栏标记 V03-10 M4 收窄为「设定集」一枚。 */
type RailPanel = "ledger";

const RAIL_LABELS: Record<RailPanel, string> = {
  ledger: "设定集",
};

const SOURCE_KIND: SourceKind = "formal";

/**
 * 对话自由输入框（V03-10 M1）：桌面唯一输入面，回车发送一轮，Shift+Enter 换行。
 *
 * 编排复用 chat store 的 `setDraft`/`send`，不复制逻辑；草稿按会话保存。
 * 连接未就绪时不发送，提示去模型设置。发送中（本会话有活动轮次）禁用输入与按钮。
 */
function ConversationComposer({
  conversationId,
  onOpenModels,
}: {
  conversationId: string;
  onOpenModels?: () => void;
}) {
  const draft = useChatStore((s) => s.drafts[conversationId] ?? "");
  const setDraft = useChatStore((s) => s.setDraft);
  const send = useChatStore((s) => s.send);
  const serviceKind = useChatStore((s) => s.serviceKind);
  const busy = useChatStore((s) =>
    Boolean(s.views[conversationId]?.activeTurnId),
  );
  const ready = serviceKind === "api" || serviceKind === "simulator";
  const canSend = ready && !busy && draft.trim() !== "";

  function submit() {
    if (!canSend) return;
    void send();
  }

  return (
    <div className="border-t border-line bg-white/70 px-3 py-2">
      <div className="flex items-end gap-2">
        <textarea
          aria-label="对话输入框"
          className="well-input min-h-[44px] max-h-32 flex-1 resize-y p-2 text-sm leading-7"
          placeholder="用一句话告诉 Agent 想写什么、改什么…（回车发送，Shift+Enter 换行）"
          value={draft}
          disabled={!ready || busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !(e.nativeEvent as KeyboardEvent).isComposing
            ) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button
          className="btn shrink-0 px-4 py-2 text-xs"
          disabled={!canSend}
          onClick={submit}
        >
          {busy ? "回复中…" : "发送"}
        </button>
      </div>
      {!ready && (
        <p className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-sub">
          <span>
            {serviceKind === "unsupported"
              ? "未连接模型服务；连接后可在对话里改稿。"
              : "正在检测模型服务…"}
          </span>
          {onOpenModels && (
            <button className="text-blue underline" onClick={onOpenModels}>
              打开模型设置
            </button>
          )}
        </p>
      )}
    </div>
  );
}

/**
 * 写作台讨论区（V03-1.5 接线 + V03-9 §4 候选链路 + V03-10 M1 自由输入）。
 *
 * 呈现会话消息与流式回复，并复用 ChatCards 渲染任务建议/执行/候选/结果卡，
 * 「对话中发起创作 → 候选采用」这条链路在写作台内闭环；底部自由输入框发送一轮。
 */
function DeskDiscussion({
  conversationId,
  onOpenCandidate,
  onOpenModels,
}: {
  conversationId: string;
  onOpenCandidate(messageId: string): void;
  onOpenModels?: () => void;
}) {
  const view = useChatStore((s) => s.views[conversationId] ?? null);
  const serviceKind = useChatStore((s) => s.serviceKind);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const retryUnknownSend = useChatStore((s) => s.retryUnknownSend);
  const stop = useChatStore((s) => s.stop);
  const acceptCandidate = useChatStore((s) => s.acceptCandidate);
  const adjustDraft = useChatStore((s) => s.adjustDraft);
  const startDraft = useChatStore((s) => s.startDraft);
  const abandonDraft = useChatStore((s) => s.abandonDraft);
  const cancelTask = useChatStore((s) => s.cancelTask);
  const retryTask = useChatStore((s) => s.retryTask);

  // 打开讨论视图即载入并激活该会话（原由对话侧栏承担）；服务就绪后补一次。
  useEffect(() => {
    if (!conversationId || conversationId === "pending") return;
    if (activeConversationId === conversationId) return;
    const chat = useChatStore.getState();
    if (!chat.service) return; // 服务探测完成后由 serviceKind 变化重跑
    void chat.selectConversation(conversationId);
  }, [conversationId, serviceKind, activeConversationId]);

  if (!view) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <Empty>这条讨论还没有载入。从左侧「对话历史」重新打开。</Empty>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-4" aria-label="讨论消息">
        {view.messages.length === 0 && !view.loading && (
          <Empty>还没有对话记录。在下方输入框写一句，让 Agent 开始。</Empty>
        )}
        {view.messages.map((m) => {
          if (m.kind === "task_draft")
            return (
              <div key={m.message_id} className="mb-3">
                <TaskDraftCard
                  message={m}
                  abandoned={
                    view.abandoned[
                      (m.payload as { draft_id: string }).draft_id
                    ] ?? false
                  }
                  onAdjust={(id, patch) => void adjustDraft(id, patch)}
                  onStart={(id) => void startDraft(id)}
                  onAbandon={(id) => void abandonDraft(id)}
                />
              </div>
            );
          if (m.kind === "task_execution")
            return (
              <div key={m.message_id} className="mb-3">
                <TaskExecutionCard
                  message={m}
                  onView={(candidateId) => onOpenCandidate(candidateId)}
                  onCancel={(jobRef) => void cancelTask(jobRef)}
                  onRetry={(jobRef) => void retryTask(jobRef)}
                />
              </div>
            );
          if (m.kind === "candidate")
            return (
              <div key={m.message_id} className="mb-3">
                <CandidateCard
                  message={m}
                  onReview={() => onOpenCandidate(m.message_id)}
                  onAccept={(id) => void acceptCandidate(id)}
                />
              </div>
            );
          if (m.kind !== "text")
            return (
              <div key={m.message_id} className="mb-3">
                <ResultCard message={m} />
              </div>
            );
          return (
            <article
              key={m.message_id}
              className={`mb-3 max-w-[85%] rounded-lg px-4 py-2 text-sm leading-7 ${
                m.role === "author" ? "ml-auto bg-blue-50 text-left" : "bg-white/80"
              }`}
              aria-label={m.role === "author" ? "作者消息" : "助手消息"}
            >
              <span className="mb-1 block text-[10px] text-sub">
                {m.role === "author" ? "我" : m.model ?? "助手"}
                {m.status === "streaming" && " · 回复中…"}
                {m.status === "failed" && " · 失败"}
                {m.status === "stopped" && " · 已停止"}
              </span>
              <span className="whitespace-pre-wrap">{m.text}</span>
              {m.status === "streaming" && (
                <span className="chat-cursor" aria-hidden />
              )}
              {m.status === "stopped" && (
                <span className="mt-1 block text-[10px] text-sub">
                  以上是已收到的部分回复。
                </span>
              )}
            </article>
          );
        })}
        {view.error && (
          <p className="notice p-3 text-xs text-red-600" role="alert">
            {view.error}
          </p>
        )}
        {view.pending && (
          <div className="notice p-3 text-xs" role="alert">
            <p>{view.pending.error}</p>
            <button
              className="mt-1 text-blue underline"
              onClick={() => void retryUnknownSend()}
            >
              用同一请求重试
            </button>
          </div>
        )}
      </div>
      {view.activeTurnId && (
        <div className="border-t border-line px-4 py-2">
          <button
            className="btn btn-gray px-3 py-1.5 text-xs"
            onClick={() => void stop()}
          >
            停止回复
          </button>
        </div>
      )}
      <ConversationComposer
        conversationId={conversationId}
        onOpenModels={onOpenModels}
      />
    </div>
  );
}

export default function DeskShell({ onOpenModels }: { onOpenModels?: () => void }) {
  const v2 = useV2Store();
  const pid = v2.currentId;
  const mainView = useDeskStore((s) => s.mainView);
  // 左栏「对话历史」：当前项目的会话列表与当前选中会话（chat store 提供）。
  const conversations = useChatStore(activeConversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);

  // 项目切换同步壳层状态（换项目：旧操作不带入，主区回到未定焦点）。
  useEffect(() => {
    useDeskStore.getState().setProject(pid ?? null);
    setRailPanel(null);
  }, [pid]);

  // 对话服务绑定（原由对话侧栏承担）：换作品时重新绑定项目并载入首个会话。
  useEffect(() => {
    void useChatStore.getState().bindProject(pid ?? null);
  }, [pid]);

  // 模拟器只读快照：当前章 / 已接受节拍 / 当前模型（不写入任何项目内容）。
  const chatService = useChatStore((s) => s.service);
  useEffect(() => {
    if (!chatService || chatService.kind !== "simulator") return;
    simulator.setView({
      hasAcceptedBeats: v2.storyArtifacts.some((a) => a.kind === "narrative.beat"),
      currentChapter: v2.chapter.chapterId
        ? {
            chapter_id: v2.chapter.chapterId,
            title: v2.chapter.title,
            base: v2.chapter.chapterBase,
          }
        : null,
    });
  }, [
    chatService,
    v2.storyArtifacts,
    v2.chapter.chapterId,
    v2.chapter.title,
    v2.chapter.chapterBase,
  ]);

  // 初始焦点：自取正文目录判定（App 的 refreshManuscript 异步，无法区分
  // 「空目录」与「未加载」），一次性决定；之后由作者显式导航。
  const [initialChapters, setInitialChapters] = useState<string[] | null>(null);
  useEffect(() => {
    if (!pid) {
      setInitialChapters(null);
      return;
    }
    let alive = true;
    setInitialChapters(null);
    fetchManuscript(pid)
      .then((d) => {
        if (alive) setInitialChapters(d.chapters.map((c) => c.chapter_id));
      })
      .catch(() => {
        if (alive) setInitialChapters([]);
      });
    return () => {
      alive = false;
    };
  }, [pid]);
  useEffect(() => {
    if (!pid || initialChapters === null) return;
    const d = useDeskStore.getState();
    if (d.projectId !== pid || d.mainView !== null) return;
    if (initialChapters.length > 0) {
      const chapterId = selectInitialChapterId(initialChapters, d, pid);
      d.navigate(
        chapterId
          ? { kind: "manuscript", chapterId }
          : { kind: "discussion", conversationId: "pending" },
      );
    } else {
      // 无正文（或只有未采用候选）：构思状态（M01 第 1 条）。
      d.navigate({ kind: "discussion", conversationId: "pending" });
    }
  }, [pid, initialChapters]);

  // 章节打开后恢复阅读位置（四元组键，M01 第 3 条）。
  const scrollRef = useRef<HTMLDivElement>(null);
  const chapterLoading = v2.chapter.loading;
  useEffect(() => {
    if (mainView?.kind !== "manuscript" || !pid || chapterLoading) return;
    const cid = mainView.chapterId;
    const saved = Object.values(useDeskStore.getState().readingPositions).find(
      (p) =>
        p.projectId === pid &&
        p.chapterId === cid &&
        p.sourceKind === SOURCE_KIND &&
        p.editSessionId === null,
    );
    if (saved?.scrollTop != null && scrollRef.current) {
      scrollRef.current.scrollTop = saved.scrollTop;
    }
    // chapterId 相同但内容刚载入时也要恢复一次。
  }, [mainView, pid, chapterLoading, v2.chapter.chapterId]);

  const scrollTimer = useRef<number | null>(null);
  function onManuscriptScroll(el: React.UIEvent<HTMLDivElement>) {
    if (mainView?.kind !== "manuscript" || !pid) return;
    if (scrollTimer.current !== null) window.clearTimeout(scrollTimer.current);
    const target = el.currentTarget;
    const cid = mainView.chapterId;
    scrollTimer.current = window.setTimeout(() => {
      useDeskStore.getState().saveReadingPosition({
        projectId: pid,
        chapterId: cid,
        sourceKind: SOURCE_KIND,
        editSessionId: null,
        scrollTop: target.scrollTop,
      });
    }, 300);
  }

  function openChapter(chapterId: string) {
    void v2.selectChapter(chapterId);
    useDeskStore.getState().navigate({ kind: "manuscript", chapterId });
  }

  const [importOpen, setImportOpen] = useState(false);
  // 右栏（M3）：默认不渲染面板；标记在稿纸右缘，点开为抽屉，Esc/遮罩关闭。
  const [railPanel, setRailPanel] = useState<RailPanel | null>(null);
  // 左导航抽屉（中屏以下）：断点行为与既有一致。
  const [navDrawer, setNavDrawer] = useState(false);
  useEffect(() => {
    if (!navDrawer && !railPanel) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setNavDrawer(false);
        setRailPanel(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navDrawer, railPanel]);
  const chapter = v2.chapter;

  // 候选审阅（V03-9 §4）：从讨论区的候选卡/任务卡点入主区临时视图，返回回到原视图。
  const views = useChatStore((s) => s.views);
  const candidateMessage = useMemo<ChatMessage | null>(() => {
    if (mainView?.kind !== "detail" || mainView.detailKind !== "candidate")
      return null;
    const id = mainView.id;
    for (const v of Object.values(views)) {
      const m = v.messages.find((x) => x.message_id === id);
      if (m) return m;
    }
    return null;
  }, [mainView, views]);
  const candidateReturn = useRef<MainView | null>(null);
  function openCandidate(messageId: string) {
    const desk = useDeskStore.getState();
    candidateReturn.current = desk.mainView;
    desk.navigate({ kind: "detail", detailKind: "candidate", id: messageId });
  }
  function closeCandidate() {
    const back = candidateReturn.current;
    candidateReturn.current = null;
    useDeskStore
      .getState()
      .navigate(
        back && back.kind !== "detail"
          ? back
          : { kind: "discussion", conversationId: "pending" },
      );
  }
  /** 采用成功后回到受影响正文（能定位到章）；定位不到就回原视图。 */
  function finishCandidate(result: { target_ids: string[] }) {
    const chapters = useV2Store.getState().chapters;
    const target = result.target_ids.find((id) =>
      chapters.some((c) => c.chapter_id === id),
    );
    if (!target) {
      closeCandidate();
      return;
    }
    candidateReturn.current = null;
    void useV2Store.getState().selectChapter(target);
    useDeskStore.getState().navigate({ kind: "manuscript", chapterId: target });
  }

  /** 打开一条历史对话：切换会话并显示讨论（V03-10 M1 左栏「对话历史」）。 */
  async function openConversation(conversationId: string) {
    await useChatStore.getState().selectConversation(conversationId);
    useDeskStore.getState().navigate({ kind: "discussion", conversationId });
  }

  // 右栏标记（V03-10 M4）：只剩「设定集」一枚。
  const railMarks: { key: RailPanel; label: string }[] = [
    { key: "ledger", label: "设定集" },
  ];

  return (
    <main className="flex h-full min-h-0" aria-label="写作台">
      <aside
        className={`min-h-0 w-60 space-y-2 overflow-y-auto p-2 ${
          navDrawer
            ? "fixed inset-y-0 left-0 z-40 bg-page shadow-2xl lg:static lg:z-auto lg:flex-none lg:bg-transparent lg:shadow-none"
            : "hidden lg:block lg:flex-none"
        }`}
      >
          <section className="panel" aria-label="章节目录">
            <header className="panel-head flex items-center justify-between px-4 py-2">
              <span>
                章节目录 <small>{v2.chapters.length} 章</small>
              </span>
              <button
                className="text-[11px] text-blue underline disabled:text-sub disabled:no-underline"
                disabled={!pid || chapter.saving}
                onClick={() => setImportOpen(true)}
              >
                导入正文
              </button>
            </header>
            {v2.chapters.length === 0 ? (
              <div className="px-4 py-2 text-xs text-sub">
                <p>
                  还没有正文。可以导入 .txt / .md 稿件，或在下方告诉 Agent
                  想写什么。
                </p>
              </div>
            ) : (
              v2.chapters.map((c) => (
                <button
                  key={c.chapter_id}
                  className={`asset-row block w-full px-4 py-2 text-left text-xs ${
                    mainView?.kind === "manuscript" &&
                    mainView.chapterId === c.chapter_id
                      ? "sel"
                      : ""
                  }`}
                  disabled={chapter.saving}
                  onClick={() => openChapter(c.chapter_id)}
                >
                  {c.title}
                </button>
              ))
            )}
          </section>
          <section className="panel" aria-label="对话历史">
            <header className="panel-head flex items-center justify-between px-4 py-2">
              <span>
                对话历史 <small>{conversations.length} 条</small>
              </span>
            </header>
            {conversations.length === 0 ? (
              <p className="px-4 py-2 text-xs text-sub">还没有对话记录。</p>
            ) : (
              conversations.map((cv) => (
                <button
                  key={cv.conversation_id}
                  className={`asset-row block w-full truncate px-4 py-2 text-left text-xs ${
                    cv.conversation_id === activeConversationId ? "sel" : ""
                  }`}
                  onClick={() => void openConversation(cv.conversation_id)}
                >
                  {cv.title || "未命名对话"}
                </button>
              ))
            )}
          </section>
      </aside>

      <section className="panel flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="panel-head flex-wrap gap-2 px-4 py-2">
          <h1 className="text-sm">
            {mainView?.kind === "manuscript"
              ? chapter.title || "正文"
              : mainView?.kind === "discussion"
                ? "构思与协作"
                : mainView?.kind === "detail" &&
                    mainView.detailKind === "candidate"
                  ? "候选审阅"
                  : "写作台"}
          </h1>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-gray px-3 py-1.5 text-xs lg:hidden"
              aria-label={navDrawer ? "关闭目录" : "打开目录"}
              onClick={() => setNavDrawer((v) => !v)}
            >
              目录
            </button>
            <button
              type="button"
              className="btn btn-gray px-3 py-1.5 text-xs lg:hidden"
              aria-label="打开侧栏"
              onClick={() => setRailPanel("ledger")}
            >
              侧栏
            </button>
          </div>
        </header>
        <div className="px-3 pt-2">
          <ErrorText>{chapter.error}</ErrorText>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {mainView?.kind === "manuscript" ? (
            chapter.loading ? (
              <Empty>正在读取正文…</Empty>
            ) : chapter.chapterId && chapter.draft !== null ? (
              <div
                ref={scrollRef}
                className="relative h-full overflow-auto bg-[#d4dadd] p-2 lg:p-3"
                onScroll={onManuscriptScroll}
              >
                <textarea
                  aria-label="人工正文"
                  className="paper-sheet mx-auto min-h-full w-full max-w-[44em] resize-none px-6 py-8 font-serif text-[18px] leading-8 lg:px-10"
                  spellCheck={false}
                  readOnly
                  value={chapter.draft}
                />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center">
                <Empty>此章暂无正文内容。</Empty>
              </div>
            )
          ) : mainView?.kind === "discussion" ? (
            mainView.conversationId === "pending" && !activeConversationId ? (
              <div className="h-full overflow-y-auto p-4">
                <div className="notice p-4 text-xs leading-6">
                  <strong>构思状态。</strong>
                  这个作品还没有正式正文——在对话里告诉 Agent 想写什么，
                  生成的正文会以候选形式回到对话里，由你采用后落盘。
                </div>
              </div>
            ) : (
              <DeskDiscussion
                conversationId={
                  mainView.conversationId === "pending"
                    ? activeConversationId!
                    : mainView.conversationId
                }
                onOpenCandidate={openCandidate}
                onOpenModels={onOpenModels}
              />
            )
          ) : mainView?.kind === "detail" && mainView.detailKind === "candidate" &&
            pid &&
            candidateMessage ? (
            <div className="h-full overflow-y-auto p-2">
              <ChatCandidateReview
                key={candidateMessage.message_id}
                candidate={candidateMessage}
                projectId={pid}
                onClose={closeCandidate}
                onAccepted={finishCandidate}
              />
            </div>
          ) : mainView?.kind === "detail" && mainView.detailKind === "candidate" ? (
            <div className="flex h-full items-center justify-center p-4">
              <Empty>候选内容还没有载入。回到讨论区重新打开。</Empty>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center p-4">
              <Empty>
                {!pid
                  ? "先在顶部选择或新建作品，然后在对话里告诉 Agent 想写什么。"
                  : "正在确定初始视图…"}
              </Empty>
            </div>
          )}
        </div>
      </section>

      <aside
        className="hidden min-h-0 w-16 flex-none flex-col gap-1 border-l border-line bg-white/40 p-1 lg:flex"
        aria-label="侧栏标记"
      >
        {railMarks.map((mark) => (
          <button
            key={mark.key}
            type="button"
            aria-label={RAIL_LABELS[mark.key]}
            aria-pressed={railPanel === mark.key}
            className={`rail-mark asset-row px-1 py-2 text-[11px] ${
              railPanel === mark.key ? "sel" : ""
            }`}
            onClick={() => setRailPanel((p) => (p === mark.key ? null : mark.key))}
          >
            {mark.label}
          </button>
        ))}
      </aside>

      {railPanel && (
        <>
          <div
            className="fixed inset-0 z-30 bg-overlay"
            aria-hidden
            onClick={() => setRailPanel(null)}
          />
          <aside
            className="rail-drawer fixed inset-y-0 right-0 z-40 flex w-[min(400px,92vw)] min-h-0 flex-col overflow-hidden border-l border-line bg-page p-2 shadow-2xl"
            role="dialog"
            aria-label={RAIL_LABELS[railPanel]}
          >
            <div className="flex items-center justify-between px-2 py-1">
              <h2 className="text-sm">{RAIL_LABELS[railPanel]}</h2>
              <button
                type="button"
                className="btn btn-gray px-2 py-0.5 text-xs"
                aria-label="关闭侧栏"
                onClick={() => setRailPanel(null)}
              >
                关闭
              </button>
            </div>
            <div className="flex flex-wrap gap-1 px-1 pb-1 lg:hidden">
              {railMarks.map((mark) => (
                <button
                  key={mark.key}
                  type="button"
                  className={`btn px-2 py-0.5 text-[11px] ${
                    railPanel === mark.key ? "" : "btn-gray"
                  }`}
                  onClick={() => setRailPanel(mark.key)}
                >
                  {mark.label}
                </button>
              ))}
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
              {railPanel === "ledger" &&
                (pid ? (
                  <LedgerPanel projectId={pid} />
                ) : (
                  <section className="panel" aria-label="设定集">
                    <header className="panel-head px-4 py-2">设定集</header>
                    <p className="px-4 py-2 text-xs text-sub">
                      先在顶部选择或新建作品；设定会保存在作品里。
                    </p>
                  </section>
                ))}
            </div>
          </aside>
        </>
      )}

      {navDrawer && (
        <div
          className="fixed inset-0 z-30 bg-overlay lg:hidden"
          aria-hidden
          onClick={() => setNavDrawer(false)}
        />
      )}

      {pid && (
        <ImportDialog
          projectId={pid}
          open={importOpen}
          onOpenChange={setImportOpen}
          onImported={(ids, operationId) => {
            if (operationId) useDeskStore.getState().setLastOperation(operationId);
            void v2.refreshManuscript().then(() => {
              const first = ids[0];
              if (!first) return;
              void v2.selectChapter(first);
              useDeskStore
                .getState()
                .navigate({ kind: "manuscript", chapterId: first });
            });
          }}
        />
      )}
    </main>
  );
}
