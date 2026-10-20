/**
 * 三部分工作区壳（M01/V03-1.1 壳层接线）：左章节/想法、中正文+唯一输入框、
 * 右账本概要与操作结果。
 *
 * 不变量全部消费 state/desk.ts 纯函数，不在组件里重写：
 * - 主区一次只展开一个（MainView），navigate 是唯一变更入口；
 * - 初始焦点 selectInitialChapterId（有正文恢复上次章节，无记录取第一章，
 *   无正文按构思状态）；
 * - 阅读位置四元组（project|chapter|formal|-），滚动保存、打开恢复；
 * - 专注写作收起两侧，保留正文与输入框。
 *
 * 响应式（M14 / V03-8.5 走查）：宽屏（≥1280）三部分；中屏（1024–1279）折叠
 * 左导航为抽屉、保留右栏；小屏（<1024）主区优先，左右都走抽屉。断点以可读性
 * 确定，不把三列按比例缩小。临时视图只列已有真实视图的入口（无伪入口）。
 *
 * 关联图谱（graph 视图）已在 V03-6.4 接真实投影：侧栏小图 + 主区展开共用同一视图。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDeskState,
  toOperationEnvelope,
  type DeskState,
} from "../../api/desk";
import type { ChatMessage } from "../../api/chat";
import { fetchManuscript } from "../../api/v2";
import { simulator, useChatStore } from "../../state/chat";
import {
  selectInitialChapterId,
  type MainView,
  type SourceKind,
} from "../../state/desk";
import { useDeskStore, type EntryReceipt } from "../../state/deskStore";
import type { GraphViewId } from "../../state/graphLayout";
import { isDirty, useV2Store } from "../../state/v2";
import { ConfirmDiscard, Empty, ErrorText, Pill } from "../Workbench";
import {
  CandidateCard,
  ResultCard,
  TaskDraftCard,
  TaskExecutionCard,
} from "../chat/ChatCards";
import ChatCandidateReview from "../chat/ChatCandidateReview";
import DeskComposer from "./DeskComposer";
import ChapterHistory from "./ChapterHistory";
import EvidenceCheck from "./EvidenceCheck";
import GraphOverview from "./GraphOverview";
import GraphPanel from "./GraphPanel";
import RevisionPanel from "./RevisionPanel";
import ImportDialog from "./ImportDialog";
import LedgerPanel from "./LedgerPanel";
import OperationFollowups from "./OperationFollowups";
import { useEditSession } from "./useEditSession";

type RailPanel =
  | "ledger"
  | "graph"
  | "followups"
  | "results"
  | "revision"
  | "history"
  | "evidence";

/** 右栏抽屉标题；标记在稿纸右缘常驻或有内容时出现，点开为抽屉，关闭不留痕。 */
const RAIL_LABELS: Record<RailPanel, string> = {
  ledger: "故事账本",
  graph: "关联图谱",
  followups: "后续状态",
  results: "操作结果",
  revision: "整体调整",
  history: "版本历史",
  evidence: "证据核对",
};

const SOURCE_KIND: SourceKind = "formal";

/**
 * 写作台讨论区（V03-1.5 接线 + V03-9 §4 候选链路）。
 *
 * 只读呈现会话消息与流式回复，并复用 ChatCards 渲染任务建议/执行/候选/结果卡，
 * 「对话中发起创作 → 候选采用」这条链路在写作台内闭环。输入与编排仍走唯一输入框
 * 与 chat store，此处不复制对话编排。
 */
function DeskDiscussion({
  conversationId,
  onOpenCandidate,
}: {
  conversationId: string;
  onOpenCandidate(messageId: string): void;
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
        <Empty>这条讨论还没有载入。从左侧「想法与讨论」重新打开。</Empty>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-4" aria-label="讨论消息">
        {view.messages.length === 0 && !view.loading && (
          <Empty>还没有讨论记录。从左侧想法点「接着讨论」开始。</Empty>
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
    </div>
  );
}

/**
 * 后续状态（V03-4.5）：摘要待更新、待确认条数、正在修改的章。
 *
 * 数字来自 `/desk-state`，重开页面也在；条目正文不在这里复制——点进去回
 * 候选组或章节本身。没有内容时整块不渲染（空列表不占一张卡片）。
 */
function DeskFollowupSummary({ state }: { state: DeskState }) {
  const { summaries, pending_candidates, open_edit_chapters } = state.counts;
  const rows: { text: string; tone: "amber" | "blue" }[] = [];
  const total = summaries.fresh + summaries.stale + summaries.missing;
  if (summaries.stale > 0) {
    rows.push({ text: `摘要待更新（${summaries.stale} 章）`, tone: "amber" });
  } else if (total > 0 && summaries.missing > 0) {
    rows.push({ text: `还有 ${summaries.missing} 章没有摘要`, tone: "blue" });
  }
  if (pending_candidates > 0) {
    rows.push({ text: `待确认 ${pending_candidates} 条设定候选`, tone: "amber" });
  }
  if (open_edit_chapters > 0) {
    rows.push({ text: `修改中（${open_edit_chapters} 章）`, tone: "amber" });
  }
  if (rows.length === 0) return null;
  return (
    <section className="panel" aria-label="后续状态">
      <header className="panel-head px-4 py-2">后续状态</header>
      {rows.map((row) => (
        <p key={row.text} className="asset-row px-4 py-2 text-xs">
          <Pill tone={row.tone}>{row.text}</Pill>
        </p>
      ))}
    </section>
  );
}

export default function DeskShell({ onOpenModels }: { onOpenModels?: () => void }) {
  const v2 = useV2Store();
  const pid = v2.currentId;
  const mainView = useDeskStore((s) => s.mainView);
  const entryReceipts = useDeskStore((s) => s.entryReceipts);
  const operations = useDeskStore((s) => s.operations);
  const lastOperationId = useDeskStore((s) => s.lastOperationId);

  // 项目切换同步壳层状态（换项目：旧操作/回执不带入，主区回到未定焦点）。
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

  // 侧栏共享状态（V03-4.5）：计数与最近结果都来自权威存储，重开页面也在。
  // 最近结果按 operationId 灌回同一份缓存，不新加一份「历史列表」。
  const [desk, setDesk] = useState<DeskState | null>(null);
  useEffect(() => {
    if (!pid) {
      setDesk(null);
      return;
    }
    let alive = true;
    fetchDeskState(pid)
      .then((state) => {
        if (!alive) return;
        setDesk(state);
        for (const row of state.recent_operations) {
          useDeskStore.getState().applyOperationResult(toOperationEnvelope(pid, row));
        }
      })
      .catch(() => {
        // 读不到侧栏状态不影响正文与输入；打开具体结果时会显示真实错误。
        if (alive) setDesk(null);
      });
    return () => {
      alive = false;
    };
    // lastOperationId：本次刚完成的操作会改变计数，顺带刷新一次。
  }, [pid, lastOperationId]);

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
    if (isDirty(v2.chapter)) {
      setPending(chapterId);
      return;
    }
    void v2.selectChapter(chapterId);
    useDeskStore.getState().navigate({ kind: "manuscript", chapterId });
  }

  const [pending, setPending] = useState<string | null>(null);
  const [continueError, setContinueError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [abandonPending, setAbandonPending] = useState(false);
  // 想法与讨论默认折叠为区块头 + 条数（M4）；展开状态不持久化，每次进入回到折叠。
  const [ideasOpen, setIdeasOpen] = useState(false);
  // 图谱视图（V03-6.4）：侧栏与主区共用同一选择；展开后 id 记在主区视图里。
  const [graphView, setGraphView] = useState<GraphViewId>("characters");
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
  // 账本保存后让图谱重读实际版本（产品 §5.8 第 6 条）。
  const [graphRefresh, setGraphRefresh] = useState(0);
  const chapter = v2.chapter;
  const chapterTitles = useMemo(
    () => Object.fromEntries(v2.chapters.map((c) => [c.chapter_id, c.title])),
    [v2.chapters],
  );
  const chatServiceKind = useChatStore((s) => s.serviceKind);
  const edit = useEditSession({
    projectId: pid,
    chapterId: mainView?.kind === "manuscript" ? mainView.chapterId : null,
    formalText: chapter.draft ?? "",
    onFinished: (result) => {
      useDeskStore.getState().setLastOperation(result.operation_id);
      void v2.refreshManuscript();
      const first = result.changed_chapter_ids[0];
      if (first) void v2.selectChapter(first);
    },
  });

  /** 侧栏切换图谱视图：只切选择，不夺走主区（数据事件与作者导航分离，M01）。 */
  function switchGraphView(view: GraphViewId) {
    setGraphView(view);
  }

  /** 打开/切换主区图谱视图：复用 detail 槽位，主区一次只展开一个。 */
  function openGraphView(view: GraphViewId) {
    setGraphView(view);
    useDeskStore.getState().navigate({ kind: "detail", detailKind: "graph", id: view });
  }

  /** 「加入讨论」：把图谱选中对象作为可见引用附到唯一输入框（V03-6.4）。 */
  function addToDiscussion(reference: string) {
    useDeskStore.getState().requestComposerInsert(reference);
  }

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

  /** 打开讨论：绑定作品并载入会话（不发送）。 */
  async function openDiscussion(r: EntryReceipt) {
    if (!r.conversationId) return;
    setContinueError(null);
    try {
      const chat = useChatStore.getState();
      if (chat.projectId !== r.projectId) await chat.bindProject(r.projectId);
      await useChatStore.getState().selectConversation(r.conversationId);
      useDeskStore
        .getState()
        .navigate({ kind: "discussion", conversationId: r.conversationId });
    } catch (e) {
      setContinueError(e instanceof Error ? e.message : String(e));
    }
  }

  /**
   * 接着讨论（V03-1.5）：显式作者动作——引用这条已保存想法启动轮次。
   * 零自动重放：只有点这里才调用模型；成功后焦点回到唯一输入框。
   */
  async function continueIdea(r: EntryReceipt) {
    if (!r.conversationId) {
      setContinueError("这条想法还没有讨论会话，请重新保存后再继续。");
      return;
    }
    setContinueError(null);
    try {
      const chat = useChatStore.getState();
      if (chat.projectId !== r.projectId) await chat.bindProject(r.projectId);
      await useChatStore.getState().selectConversation(r.conversationId);
      const ok = await useChatStore
        .getState()
        .continueFromMessages(r.conversationId, [r.messageId]);
      if (!ok) return; // 错误已进会话视图/续传待重试，不打断当前阅读。
      useDeskStore
        .getState()
        .navigate({ kind: "discussion", conversationId: r.conversationId });
      useDeskStore.getState().requestComposerFocus();
    } catch (e) {
      setContinueError(e instanceof Error ? e.message : String(e));
    }
  }

  const continueEnabled = chatServiceKind === "api";

  // 右栏标记（M3）：账本/图谱/整体调整常驻；后续/结果有内容才出现，空列表不占标记。
  const resultsCount = Object.keys(operations).length;
  const summaries = desk?.counts.summaries;
  const followupCount = desk
    ? desk.counts.pending_candidates +
      desk.counts.open_edit_chapters +
      (summaries && summaries.stale + summaries.missing > 0 ? 1 : 0)
    : 0;
  const railMarks: { key: RailPanel; label: string }[] = [
    { key: "ledger", label: "账本" },
    { key: "graph", label: "图谱" },
    { key: "revision", label: "修订" },
    ...(followupCount > 0
      ? [{ key: "followups" as RailPanel, label: `后续 ${followupCount}` }]
      : []),
    ...(resultsCount > 0
      ? [{ key: "results" as RailPanel, label: `结果 ${resultsCount}` }]
      : []),
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
                disabled={!pid || chapter.saving || edit.active}
                title={edit.active ? "完成或暂存后可切换章节" : undefined}
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
                  disabled={chapter.saving || edit.active}
                  onClick={() => openChapter(c.chapter_id)}
                >
                  {c.title}
                </button>
              ))
            )}
          </section>
          <section className="panel" aria-label="想法与讨论">
            <header className="panel-head">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-2 text-left"
                aria-expanded={ideasOpen}
                onClick={() => setIdeasOpen((v) => !v)}
              >
                <span>
                  想法与讨论 <small>{entryReceipts.length} 条</small>
                </span>
                <span aria-hidden className="text-[10px]">
                  {ideasOpen ? "▴" : "▾"}
                </span>
              </button>
            </header>
            {ideasOpen &&
              (entryReceipts.length === 0 ? (
                <p className="px-4 py-2 text-xs text-sub">
                  保存的想法会出现在这里。
                </p>
              ) : (
                entryReceipts.map((r) => (
                  <div
                    key={r.messageId}
                    className="asset-row block w-full px-4 py-3 text-left text-xs"
                  >
                    <button
                      className="block w-full text-left"
                      onClick={() => void openDiscussion(r)}
                      aria-label={`打开讨论：${r.text.slice(0, 20)}`}
                    >
                      <span className="block truncate">{r.text}</span>
                      <span className="text-[10px] text-sub">
                        {r.savedAt} · {r.status === "not_requested" ? "未请求模型" : r.status}
                      </span>
                    </button>
                    <button
                      className="mt-1 text-[11px] text-blue underline disabled:cursor-not-allowed disabled:text-sub disabled:no-underline"
                      disabled={!continueEnabled || !r.conversationId}
                      title={
                        continueEnabled
                          ? "引用这条想法开始一轮回复"
                          : "连接模型后才能接着讨论（模拟模式不支持真实保存的想法）"
                      }
                      onClick={() => void continueIdea(r)}
                    >
                      接着讨论
                    </button>
                  </div>
                ))
              ))}
            {ideasOpen && continueError && (
              <p className="px-4 py-2 text-[11px] text-red-600" role="alert">
                {continueError}
              </p>
            )}
            {ideasOpen && !continueEnabled && entryReceipts.length > 0 && (
              <p className="px-4 py-2 text-[10px] leading-5 text-sub">
                {chatServiceKind === "simulator"
                  ? "模拟模式下不能继续真实保存的想法；用 ?chatMock=0 走真实 API。"
                  : "连接模型后，可从任一条想法继续讨论。"}
              </p>
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
                : mainView?.kind === "detail" && mainView.detailKind === "graph"
                  ? `关联图谱 · ${mainView.id === "clues" ? "线索关联" : "人物关系"}`
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
            {mainView?.kind === "manuscript" && !edit.active && (
              <>
                {(isDirty(chapter) || chapter.saving) && (
                  <Pill tone="amber">{chapter.saving ? "保存中" : "未保存"}</Pill>
                )}
                <button
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={!chapter.chapterId || chapter.loading}
                  onClick={() => void edit.start()}
                >
                  手工修改
                </button>
                <button
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={!chapter.chapterId || chapter.loading}
                  onClick={() => setRailPanel("history")}
                >
                  历史
                </button>
                <button
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={!chapter.chapterId || chapter.loading}
                  onClick={() => setRailPanel("evidence")}
                >
                  证据核对
                </button>
              </>
            )}
            {edit.active && (
              <>
                <Pill tone={edit.conflict ? "amber" : "green"}>
                  {edit.saving
                    ? "保存中"
                    : edit.conflict
                      ? "序号冲突"
                      : `修改中 · 已保存 #${edit.seq}`}
                </Pill>
                <button
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  onClick={() => void edit.suspend()}
                >
                  暂存返回
                </button>
                <button
                  className="btn px-4 py-1.5 text-xs"
                  disabled={edit.saving || edit.conflict}
                  onClick={() => void edit.finish()}
                >
                  完成修改
                </button>
                {abandonPending ? (
                  <>
                    <span className="text-[11px] text-sub">放弃本轮修改？</span>
                    <button
                      className="btn btn-gray px-2 py-1 text-[11px]"
                      onClick={() => setAbandonPending(false)}
                    >
                      取消
                    </button>
                    <button
                      className="btn btn-gray px-2 py-1 text-[11px]"
                      onClick={() => {
                        setAbandonPending(false);
                        void edit.abandon();
                      }}
                    >
                      确认放弃
                    </button>
                  </>
                ) : (
                  <button
                    className="btn btn-gray px-3 py-1.5 text-xs"
                    onClick={() => setAbandonPending(true)}
                  >
                    放弃
                  </button>
                )}
              </>
            )}
          </div>
        </header>
        {edit.view && !edit.active && mainView?.kind === "manuscript" && (
          <div className="notice mx-3 mt-2 flex flex-wrap items-center gap-2 p-2 text-[11px]">
            <span>有一轮未完成的修改（{edit.view.drafts.length} 章草稿）。</span>
            <button className="text-blue underline" onClick={edit.resume}>
              继续修改
            </button>
            <button className="text-sub underline" onClick={() => void edit.abandon()}>
              放弃本轮
            </button>
          </div>
        )}
        <div className="px-3 pt-2">
          <ErrorText>{chapter.error}</ErrorText>
          <ErrorText>{edit.error}</ErrorText>
          {edit.conflict && (
            <p className="text-[11px] text-red-600" role="alert">
              草稿已在别处前进，自动保存已暂停。请暂存返回后重新进入修改，避免覆盖较新的内容。
            </p>
          )}
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
                  readOnly={!edit.active}
                  value={edit.active ? edit.text : chapter.draft}
                  onChange={(e) => edit.active && edit.setText(e.target.value)}
                />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center">
                <Empty>此章暂无正文内容。</Empty>
              </div>
            )
          ) : mainView?.kind === "discussion" ? (
            mainView.conversationId === "pending" ? (
              <div className="h-full overflow-y-auto p-4">
                <div className="notice p-4 text-xs leading-6">
                  <strong>构思状态。</strong>
                  这个作品还没有正式正文——下方输入框保存的想法都留在这里，
                  连接模型后可从任一条想法继续讨论。
                </div>
                {entryReceipts.length === 0 && (
                  <Empty>还没有保存过想法。在下方写下第一条构思。</Empty>
                )}
              </div>
            ) : (
              <DeskDiscussion
                conversationId={mainView.conversationId}
                onOpenCandidate={openCandidate}
              />
            )
          ) : mainView?.kind === "detail" && mainView.detailKind === "graph" && pid ? (
            <GraphPanel
              projectId={pid}
              viewId={mainView.id === "clues" ? "clues" : "characters"}
              onSwitchView={openGraphView}
              onNavigateChapter={openChapter}
              onAddToDiscussion={addToDiscussion}
              chapterTitles={chapterTitles}
              refreshKey={graphRefresh}
            />
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
                  ? "先在顶部选择或新建项目；也可以直接在下方保存想法。"
                  : "正在确定初始视图…"}
              </Empty>
            </div>
          )}
        </div>
        <DeskComposer onOpenModels={onOpenModels} />
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
                  <LedgerPanel
                    projectId={pid}
                    onSaved={() => {
                      void v2.refreshStory();
                      // 账本保存后让图谱重读实际版本（产品 §5.8 第 6 条）。
                      setGraphRefresh((tick) => tick + 1);
                    }}
                  />
                ) : (
                  <section className="panel" aria-label="故事账本">
                    <header className="panel-head px-4 py-2">故事账本</header>
                    <p className="px-4 py-2 text-xs text-sub">
                      先在顶部选择或新建作品；账本条目会保存在作品里。
                    </p>
                  </section>
                ))}
              {railPanel === "graph" && pid && (
                <GraphOverview
                  projectId={pid}
                  viewId={graphView}
                  onSwitchView={switchGraphView}
                  onExpand={(view) => {
                    openGraphView(view);
                    setRailPanel(null);
                  }}
                  refreshKey={graphRefresh}
                />
              )}
              {railPanel === "followups" && pid && (
                <>
                  {desk && <DeskFollowupSummary state={desk} />}
                  {lastOperationId && (
                    <OperationFollowups projectId={pid} operationId={lastOperationId} />
                  )}
                </>
              )}
              {railPanel === "results" && (
                <section className="panel" aria-label="操作结果">
                  <header className="panel-head px-4 py-2">操作结果</header>
                  {resultsCount === 0 ? (
                    <p className="px-4 py-2 text-xs text-sub">
                      异步任务结果会显示在这里，不改动主区与阅读位置。
                    </p>
                  ) : (
                    Object.values(operations).map((op) => (
                      <p className="asset-row px-4 py-2 text-xs" key={op.operationId}>
                        {op.operationId.slice(0, 16)}… · {op.status}
                        {op.changedChapterIds.length > 0 && (
                          <span className="block text-[10px] text-sub">
                            影响 {op.changedChapterIds.length} 章
                          </span>
                        )}
                      </p>
                    ))
                  )}
                </section>
              )}
              {railPanel === "revision" && pid && (
                <div className="flex min-h-0 flex-1 flex-col">
                  <RevisionPanel
                    projectId={pid}
                    onPublished={() => {
                      void v2.refreshManuscript();
                      void v2.refreshStory();
                    }}
                    onBack={() => {
                      const chapterId = v2.chapter.chapterId;
                      if (chapterId) {
                        useDeskStore.getState().navigate({ kind: "manuscript", chapterId });
                      }
                    }}
                  />
                </div>
              )}
              {railPanel === "history" && pid && mainView?.kind === "manuscript" && (
                <ChapterHistory
                  projectId={pid}
                  chapterId={mainView.chapterId}
                  onRestored={() => {
                    void v2.refreshManuscript();
                    void v2.selectChapter(mainView.chapterId);
                  }}
                />
              )}
              {railPanel === "evidence" && pid && mainView?.kind === "manuscript" && (
                <EvidenceCheck projectId={pid} chapterId={mainView.chapterId} />
              )}
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

      <ConfirmDiscard
        open={pending !== null}
        onCancel={() => setPending(null)}
        onDiscard={() => {
          if (pending) {
            void v2.selectChapter(pending, true);
            useDeskStore.getState().navigate({
              kind: "manuscript",
              chapterId: pending,
            });
          }
          setPending(null);
        }}
      />

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
