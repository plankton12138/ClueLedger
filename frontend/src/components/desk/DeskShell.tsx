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
 * 占位（后续任务接线）：detail 四类视图渲染占位说明（V03-2/3/6）；
 * 讨论列表与「接着讨论」（V03-1.5）；执行日志（V03-1.6）。
 */

import { useEffect, useRef, useState } from "react";
import { fetchManuscript } from "../../api/v2";
import { useChatStore } from "../../state/chat";
import {
  selectInitialChapterId,
  type SourceKind,
} from "../../state/desk";
import { useDeskStore, type EntryReceipt } from "../../state/deskStore";
import { isDirty, useV2Store } from "../../state/v2";
import { ConfirmDiscard, Empty, ErrorText, Pill, kindLabels } from "../Workbench";
import DeskComposer from "./DeskComposer";
import ChapterHistory from "./ChapterHistory";
import EvidenceCheck from "./EvidenceCheck";
import ImportDialog from "./ImportDialog";
import { useEditSession } from "./useEditSession";

const DETAIL_KINDS: { detailKind: "candidate" | "diff" | "graph" | "history"; label: string; stage: string }[] = [
  { detailKind: "candidate", label: "正文候选", stage: "V03-3" },
  { detailKind: "diff", label: "差异对比", stage: "V03-2" },
  { detailKind: "graph", label: "关联图谱", stage: "V03-6" },
  { detailKind: "history", label: "版本历史", stage: "V03-2" },
];

const SOURCE_KIND: SourceKind = "formal";

/**
 * 写作台讨论区（V03-1.5 接线）：只读呈现会话消息与流式回复。
 * 与侧栏共享同一会话事实源（chat store views），输入与停止走唯一输入框/顶部控制，
 * 任务卡等完整对话能力仍在侧栏——这里不复制对话编排。
 */
function DeskDiscussion({ conversationId }: { conversationId: string }) {
  const view = useChatStore((s) => s.views[conversationId] ?? null);
  const retryUnknownSend = useChatStore((s) => s.retryUnknownSend);
  const stop = useChatStore((s) => s.stop);
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
        {view.messages.map((m) => (
          <article
            key={m.message_id}
            className={`mb-3 max-w-[85%] rounded-lg px-4 py-2 text-xs leading-6 ${
              m.role === "author"
                ? "ml-auto bg-blue-50 text-left"
                : "bg-white/80"
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
        ))}
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

export default function DeskShell({ onOpenModels }: { onOpenModels?: () => void }) {
  const v2 = useV2Store();
  const pid = v2.currentId;
  const mainView = useDeskStore((s) => s.mainView);
  const focusMode = useDeskStore((s) => s.focusMode);
  const entryReceipts = useDeskStore((s) => s.entryReceipts);
  const operations = useDeskStore((s) => s.operations);

  // 项目切换同步壳层状态（换项目：旧操作/回执不带入，主区回到未定焦点）。
  useEffect(() => {
    useDeskStore.getState().setProject(pid ?? null);
  }, [pid]);

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
  const chapter = v2.chapter;
  const chatServiceKind = useChatStore((s) => s.serviceKind);
  const edit = useEditSession({
    projectId: pid,
    chapterId: mainView?.kind === "manuscript" ? mainView.chapterId : null,
    formalText: chapter.draft ?? "",
    onFinished: (result) => {
      void v2.refreshManuscript();
      const first = result.changed_chapter_ids[0];
      if (first) void v2.selectChapter(first);
    },
  });

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

  return (
    <main className="flex h-full min-h-0" aria-label="写作台">
      {!focusMode && (
        <aside className="min-h-0 w-60 flex-none space-y-4 overflow-y-auto p-3">
          <section className="panel" aria-label="章节目录">
            <header className="panel-head flex items-center justify-between px-4 py-3">
              <span>
                章节目录 <small>{v2.chapters.length} 章</small>
              </span>
              <button
                className="text-[11px] text-blue underline disabled:text-sub disabled:no-underline"
                disabled={!pid || chapter.saving || edit.active}
                title={edit.active ? "完成或暂存后可切换章节（本阶段只开单章修改）" : undefined}
                onClick={() => setImportOpen(true)}
              >
                导入正文
              </button>
            </header>
            {v2.chapters.length === 0 ? (
              <p className="px-4 py-3 text-xs text-sub">
                还没有正文。可以导入 .txt / .md 稿件，或在下方保存想法。
              </p>
            ) : (
              v2.chapters.map((c) => (
                <button
                  key={c.chapter_id}
                  className={`asset-row block w-full px-4 py-3 text-left text-xs ${
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
            <header className="panel-head px-4 py-3">
              想法与讨论 <small>{entryReceipts.length} 条</small>
            </header>
            {entryReceipts.length === 0 ? (
              <p className="px-4 py-3 text-xs text-sub">
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
            )}
            {continueError && (
              <p className="px-4 py-2 text-[11px] text-red-600" role="alert">
                {continueError}
              </p>
            )}
            {!continueEnabled && entryReceipts.length > 0 && (
              <p className="px-4 py-2 text-[10px] leading-5 text-sub">
                {chatServiceKind === "simulator"
                  ? "模拟模式下不能继续真实保存的想法；用 ?chatMock=0 走真实 API。"
                  : "连接模型后，可从任一条想法继续讨论。"}
              </p>
            )}
          </section>
          <section className="panel" aria-label="临时视图">
            <header className="panel-head px-4 py-3">临时视图（占位）</header>
            {DETAIL_KINDS.map((d) => (
              <button
                key={d.detailKind}
                className={`asset-row block w-full px-4 py-3 text-left text-xs ${
                  mainView?.kind === "detail" &&
                  mainView.detailKind === d.detailKind
                    ? "sel"
                    : ""
                }`}
                onClick={() =>
                  useDeskStore.getState().navigate({
                    kind: "detail",
                    detailKind: d.detailKind,
                    // 版本历史按章查看；其他临时视图仍以作品为范围。
                    id:
                      d.detailKind === "history" && v2.chapter.chapterId
                        ? v2.chapter.chapterId
                        : (pid ?? "-"),
                  })
                }
              >
                {d.label}
                <span className="ml-1 text-[10px] text-sub">（{d.stage}）</span>
              </button>
            ))}
          </section>
        </aside>
      )}

      <section className="panel flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="panel-head flex-wrap gap-2 px-4 py-3">
          <h1 className="text-sm">
            {mainView?.kind === "manuscript"
              ? chapter.title || "正文"
              : mainView?.kind === "discussion"
                ? "构思与协作"
                : mainView?.kind === "detail"
                  ? `临时视图 · ${mainView.detailKind}`
                  : "写作台"}
          </h1>
          <div className="flex items-center gap-2">
            {mainView?.kind === "manuscript" && !edit.active && (
              <>
                <Pill tone={isDirty(chapter) ? "amber" : "green"}>
                  {chapter.saving ? "保存中" : isDirty(chapter) ? "未保存" : "已保存"}
                </Pill>
                <button
                  className="btn px-4 py-1.5 text-xs"
                  disabled={
                    !chapter.chapterId || chapter.loading || chapter.saving || !isDirty(chapter)
                  }
                  onClick={() => void v2.saveChapter()}
                >
                  保存正文
                </button>
                <button
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={!chapter.chapterId || chapter.loading}
                  onClick={() => void edit.start()}
                >
                  手工修改
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
            <button
              className="btn btn-gray px-3 py-1.5 text-xs"
              aria-label={focusMode ? "退出专注写作" : "专注写作"}
              onClick={() => useDeskStore.getState().toggleFocus()}
            >
              {focusMode ? "展开两侧" : "专注写作"}
            </button>
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
                className="relative h-full overflow-auto bg-[#d4dadd] p-4 lg:p-6"
                onScroll={onManuscriptScroll}
              >
                <textarea
                  aria-label="人工正文"
                  className="paper-sheet min-h-full w-full resize-none px-6 py-8 font-serif text-[15px] leading-8 outline-none lg:px-10"
                  spellCheck={false}
                  value={edit.active ? edit.text : chapter.draft}
                  onChange={(e) =>
                    edit.active ? edit.setText(e.target.value) : v2.setDraft(e.target.value)
                  }
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
              <DeskDiscussion conversationId={mainView.conversationId} />
            )
          ) : mainView?.kind === "detail" ? (
            mainView.detailKind === "history" && pid && mainView.id !== pid ? (
              <ChapterHistory
                projectId={pid}
                chapterId={mainView.id}
                onRestored={() => {
                  void v2.refreshManuscript();
                  void v2.selectChapter(mainView.id);
                }}
              />
            ) : (
              <div className="flex h-full items-center justify-center p-4">
                <Empty>
                  「{DETAIL_KINDS.find((d) => d.detailKind === mainView.detailKind)?.label}」
                  在对应阶段接入真实数据，当前为占位视图。
                </Empty>
              </div>
            )
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

      {!focusMode && (
        <aside className="min-h-0 w-64 flex-none space-y-4 overflow-y-auto p-3">
          <section className="panel" aria-label="账本概要">
            <header className="panel-head px-4 py-3">相关账本</header>
            {v2.storyArtifacts.length === 0 ? (
              <p className="px-4 py-3 text-xs text-sub">
                还没有账本条目。人物、线索会随创作出现在这里。
              </p>
            ) : (
              <div className="px-4 py-3 text-xs leading-6">
                {Object.entries(
                  v2.storyArtifacts.reduce<Record<string, number>>((acc, a) => {
                    const label = kindLabels[a.kind] ?? a.kind;
                    acc[label] = (acc[label] ?? 0) + 1;
                    return acc;
                  }, {}),
                )
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([label, count]) => (
                    <p key={label}>
                      {label} · {count}
                    </p>
                  ))}
              </div>
            )}
          </section>
          {mainView?.kind === "manuscript" && pid && (
            <EvidenceCheck projectId={pid} chapterId={mainView.chapterId} />
          )}
          <section className="panel" aria-label="操作结果">
            <header className="panel-head px-4 py-3">操作结果</header>
            {Object.keys(operations).length === 0 ? (
              <p className="px-4 py-3 text-xs text-sub">
                异步任务结果会显示在这里，不改动主区与阅读位置。
              </p>
            ) : (
              Object.values(operations).map((op) => (
                <p className="asset-row px-4 py-3 text-xs" key={op.operationId}>
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
        </aside>
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
          onImported={(ids) => {
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
