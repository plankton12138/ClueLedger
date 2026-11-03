/**
 * 单一写作工作区（1.0.9 P4）。
 *
 * 左侧是全书构思与章节，中部按当前选择切换全书、章节前置区或正文编辑，
 * 辅助区在对话与页边索引之间切换。旧构思页、线索页与本章相关不再作为日常入口渲染；
 * 来源阅读与模块阅读保持原有能力。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { List, MessageSquare, PanelRight } from "lucide-react";

import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import ConversationPanel from "./ConversationPanel";
import ResizeHandle from "./ResizeHandle";
import Paper from "./Paper";
import WorkspaceModulePaper from "./WorkspaceModulePaper";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { SourceNotice, ModuleSourceReader } from "../clues/SourceReader";
import { navigate, useWorkspacePage } from "../../state/navigation";
import WritingNavigator from "../writing/WritingNavigator";
import BookPlanView from "../writing/BookPlanView";
import StartupGateView from "../writing/StartupGateView";
import StructureView from "../writing/StructureView";
import ChapterEditor from "../writing/ChapterEditor";
import ChapterMarginIndex from "../writing/ChapterMarginIndex";
import { useWritingStore } from "../../state/writing";
import { createChapter, writingKey } from "../../api/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import { errorMessage } from "../../api/errors";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, fileLoading, openingPath, ready, catalog } = useWorkspaceStore();
  const page = useWorkspacePage();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const source = useSourceLocationStore(state => state.workspaceId === currentId ? state.location : null);
  const sourceId = useSourceLocationStore(state => state.workspaceId === currentId ? state.evidenceId : null);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId === currentId ? state.assembly : null);
  const writingPage = page?.kind === "writing" && page.workspaceId === currentId ? page : null;
  const chapterId = writingPage?.chapterId ?? null;
  const prosePath = writingPage?.prosePath ?? null;
  // 模块内容是全书构思页上的一种阅读态：地址栏选了章节或正文，阅读区就按章节与正文走。
  const moduleBinding = !chapterId && !prosePath && selectedModule?.workspaceId === currentId
    ? moduleAssembly?.bindings.find(binding => binding.id === selectedModule.bindingId)
    : undefined;
  const workbench = useWritingStore(state => state.workspaceId === currentId ? state.workbench : null);
  const writingLoading = useWritingStore(state => state.workspaceId === currentId ? state.loading : false);
  const writingError = useWritingStore(state => state.workspaceId === currentId ? state.error : null);
  const loadWorkbench = useWritingStore(state => state.load);
  const refreshWorkbench = useWritingStore(state => state.refresh);
  const chapter = chapterId && workbench?.chapter?.plan?.id === chapterId ? workbench.chapter : null;
  const proseChapter = !chapterId && prosePath && workbench?.chapter?.prose?.path === prosePath ? workbench.chapter : null;
  const book = workbench?.book ?? null;
  const selectedProseEntry = prosePath
    ? [...catalog.chapters, ...catalog.characters, ...catalog.settings].find(entry => entry.path === prosePath)
    : undefined;
  const [width, setWidth] = useState(0);
  const [directory, setDirectory] = useState(false);
  const [compactChat, setCompactChat] = useState(false);
  const [indexOpen, setIndexOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const previousWorkspace = useRef<string | null>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(entries[0].contentRect.width); });
    observer.observe(node); return () => observer.disconnect();
  }, []);
  const capacity = writingLayout(width, { ...prefs, sidebarVisible: true, chatVisible: true });
  const canDockDirectory = capacity.sidebar > 0;
  const compact = capacity.kind === "single";
  const chat = Boolean(currentId && (compact ? compactChat : prefs.chatVisible));
  const plan = writingLayout(width, { ...prefs, chatVisible: chat });
  const sidebar = plan.sidebar > 0;
  const directoryVisible = canDockDirectory ? sidebar : directory;
  const hasIndex = Boolean(chapter?.index);
  const expandedPaperWidth = width - (sidebar ? plan.sidebar + 6 : 0);
  const inlineIndexAvailable = Boolean(hasIndex && !compact && expandedPaperWidth >= 980);
  const indexAuxiliary = Boolean(indexOpen && hasIndex && !chat && !inlineIndexAvailable);
  const auxiliaryVisible = chat || indexAuxiliary;
  const paperVisible = !compact || !auxiliaryVisible;

  useEffect(() => {
    if (!currentId) return;
    void loadWorkbench(currentId, { chapterId, prosePath });
  }, [currentId, chapterId, prosePath, loadWorkbench]);
  useEffect(() => {
    if (prosePath) void useWorkspaceStore.getState().openPath(prosePath);
  }, [prosePath]);
  useEffect(() => { setIndexOpen(false); }, [chapterId]);
  useEffect(() => {
    if (inlineIndexAvailable) setIndexOpen(false);
  }, [inlineIndexAvailable]);
  useEffect(() => { if (prefs.chatReveal > 0) setCompactChat(true); }, [prefs.chatReveal]);
  useEffect(() => { if (sourceId) setCompactChat(false); }, [sourceId]);
  useLayoutEffect(() => {
    if (chat && !indexAuxiliary && prefs.chatReveal > 0) container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [chat, indexAuxiliary, prefs.chatReveal]);
  useLayoutEffect(() => {
    if (previousWorkspace.current && previousWorkspace.current !== currentId) {
      setDirectory(false);
      setCompactChat(false);
      setIndexOpen(false);
    }
    previousWorkspace.current = currentId;
  }, [currentId]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);

  function selectBook() {
    if (!currentId) return;
    closeChat();
    // 全书构思与模块内容共用一个地址：切换要落在状态上，不能只改地址栏。
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId });
  }
  function selectStructure() {
    if (!currentId) return;
    closeChat();
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, prosePath: STRUCTURE_PATH });
  }
  function selectChapter(id: string) {
    if (!currentId) return;
    closeChat();
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, chapterId: id });
  }
  function selectProse(path: string) {
    if (!currentId) return;
    closeChat();
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, prosePath: path });
  }
  /** 模块内容是全书构思页上的阅读态：作者选了章节或正文，就退出它。 */
  function leaveModule() {
    if (useWorkspaceModulesStore.getState().selected) useWorkspaceModulesStore.getState().close();
  }
  function closeChat() {
    if (compact) setCompactChat(false);
    else if (useLayoutStore.getState().chatVisible) useLayoutStore.getState().toggleChat();
  }
  function toggleChat() {
    if (compact) {
      setCompactChat(value => {
        if (!value) setIndexOpen(false);
        return !value;
      });
      return;
    }
    const store = useLayoutStore.getState();
    if (store.chatVisible) store.toggleChat();
    else store.revealChat();
    setIndexOpen(false);
  }
  function toggleIndex() {
    if (inlineIndexAvailable) {
      closeChat();
      return;
    }
    if (indexOpen) { setIndexOpen(false); return; }
    closeChat();
    setCompactChat(false);
    setIndexOpen(true);
  }
  function toggleSidebar() {
    if (canDockDirectory) prefs.toggleSidebar(); else setDirectory(value => !value);
  }
  async function addChapter(title: string | null): Promise<void> {
    if (!currentId) return;
    try {
      const latest = useWritingStore.getState().workbench;
      const result = await createChapter(currentId, {
        title,
        expected_plan_revision: latest?.plan_revision ?? "empty",
        request_key: writingKey("chapter-create"),
      });
      await refreshWorkbench(currentId, { chapterId: result.chapter_id });
      leaveModule();
      navigate({ kind: "writing", workspaceId: currentId, chapterId: result.chapter_id });
    } catch (error) {
      useWorkspaceStore.getState().reportError(errorMessage(error, "新建章节"));
    }
  }
  function selectInformation(itemId: string) {
    if (!currentId || !chapterId) return;
    if (compact) setIndexOpen(false);
    navigate(
      { kind: "writing", workspaceId: currentId, chapterId, informationId: itemId },
      true,
    );
  }
  const chatLabel = running ? "对话 · 进行中" : "对话";

  function paperContent() {
    if (!currentId) {
      return !ready
        ? <div className="ui-empty" role="status"><p>正在载入作品…</p></div>
        : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>;
    }
    if (source?.source.kind === "module") return <ModuleSourceReader />;
    if (moduleBinding) return <WorkspaceModulePaper key={`${currentId}:${moduleBinding.id}`} workspaceId={currentId} binding={moduleBinding} />;
    if (writingPage?.sourceId) return <Paper file={file} openingPath={openingPath} fileLoading={fileLoading} busy={loading} />;
    if (chapterId) {
      if (chapter) {
        if (!chapter.prose) return <div className="ui-empty" role="status"><p>这一章的正文文件缺失；刷新工作台后重新选择章节。</p></div>;
        if (chapter.prose.blocked_reason === "book_intent_required") return <div className="writing-load-error"><StatusMessage tone="warning" action={<Button size="compact" onClick={() => navigate({ kind: "writing", workspaceId: currentId })}>去写全书构思</Button>}>
          [BOOK_INTENT_REQUIRED] 正文/作品：作品尚无全书构思。建议：先写一句作品意图，再回到这一章。
        </StatusMessage></div>;
        if (!chapter.prose.editable) return <div className="ui-empty" role="status"><p>这一章的正文暂不可编辑。</p></div>;
        return <ChapterEditor workspaceId={currentId} chapter={chapter} support={workbench?.model_support ?? { available: false, disabled_reason: "当前没有可用模型", supported_assists: [] }} informationId={writingPage?.informationId ?? null} />;
      }
      if (writingError) return <div className="writing-load-error"><StatusMessage tone="error" action={<Button size="compact" onClick={() => void refreshWorkbench(currentId, { chapterId })}>重试</Button>}>{writingError}</StatusMessage></div>;
      return <div className="ui-empty" role="status"><p>正在读取章节…</p></div>;
    }
    if (prosePath) {
      if (prosePath === STRUCTURE_PATH) {
        // 全书结构只读页：模型自维护，作者不编辑；调整在对话里说。
        return <StructureView workspaceId={currentId} onOpenChat={() => {
          if (compact) setCompactChat(true);
          else if (!useLayoutStore.getState().chatVisible) useLayoutStore.getState().revealChat();
        }} />;
      }
      const blocked = proseChapter?.prose?.blocked_reason;
      return <>
        {selectedProseEntry?.kind === "chapter" && blocked === "book_intent_required" && <StatusMessage tone="warning">
          作品还没有全书构思，这篇正文暂时只读；先写一句作品意图再继续。
        </StatusMessage>}
        <Paper file={file} openingPath={openingPath} fileLoading={fileLoading} busy={loading} />
      </>;
    }
    if (book && workbench) return book.intent?.trim() ? <BookPlanView
      workspaceId={currentId}
      book={book}
      planRevision={workbench.plan_revision}
      support={workbench.model_support}
      onChanged={() => refreshWorkbench(currentId, {})}
      onCreateChapter={() => addChapter(null)}
      onSelectChapter={selectChapter}
    /> : <StartupGateView onOpenChat={() => {
      // 空作品从对话起步（v1.1 M1）；窄屏时对话占满主区，宽屏则侧栏展开。
      if (compact) setCompactChat(true);
      else if (!useLayoutStore.getState().chatVisible) useLayoutStore.getState().revealChat();
    }} />;
    if (writingError) return <div className="writing-load-error"><StatusMessage tone="error" action={<Button size="compact" onClick={() => void refreshWorkbench(currentId, {})}>重试</Button>}>{writingError}</StatusMessage></div>;
    return <div className="ui-empty" role="status"><p>{writingLoading || loading ? "正在读取写作页…" : "还没有写作内容。"}</p></div>;
  }

  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      <Button size="compact" variant={directoryVisible ? "primary" : "secondary"} aria-pressed={directoryVisible} aria-expanded={directoryVisible} aria-controls="writing-sidebar"
        onClick={toggleSidebar} disabled={!currentId} disabledReason="先打开作品目录">
        <List size={16} strokeWidth={2} aria-hidden="true" />侧栏
      </Button>
      {chapterId && hasIndex && !inlineIndexAvailable && <Button size="compact" variant={indexAuxiliary ? "primary" : "secondary"} aria-pressed={indexAuxiliary}
        onClick={toggleIndex} disabled={!hasIndex} disabledReason="这一章还没有索引">
        <PanelRight size={16} strokeWidth={2} aria-hidden="true" />本章信息
      </Button>}
      <Button size="compact" variant={chat ? "primary" : "secondary"} aria-pressed={chat} aria-controls="writing-chat"
        onClick={toggleChat} disabled={!currentId} disabledReason="先打开作品目录">
        <MessageSquare size={16} strokeWidth={2} aria-hidden="true" />{chatLabel}
      </Button>
    </div>
    <div className="writing-panes workbench">
      <aside id="writing-sidebar" role="region" aria-label="写作目录" className="writing-toc module" data-overlay={!canDockDirectory} hidden={!directoryVisible}
        style={{ width: sidebar ? plan.sidebar : Math.max(prefs.sidebarWidth, SIDEBAR.min * Math.max(1, prefs.fontScale / 100)) }}
        onKeyDown={event => { if (!canDockDirectory && event.key === "Escape") { event.stopPropagation(); setDirectory(false); } }}>
        <div className="writing-side-body">
          <WritingNavigator
            book={book}
            selectedChapterId={chapterId}
            selectedProsePath={prosePath}
            loading={writingLoading}
            onBook={selectBook}
            onStructure={selectStructure}
            onChapter={selectChapter}
            onProse={selectProse}
            onCreateChapter={addChapter}
            onImported={() => { if (currentId) void refreshWorkbench(currentId, { chapterId, prosePath }); }}
            onChoose={() => { if (!canDockDirectory) { setDirectory(false); setCompactChat(false); } }}
          />
        </div>
      </aside>
      {sidebar && <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="侧栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} />}
      <section className="writing-paper module" role="region" aria-label="写作" hidden={!paperVisible}>
        <div className="flex h-full min-h-0 flex-col"><SourceNotice /><div className="min-h-0 flex-1 flex flex-col">
          {paperContent()}
        </div></div>
      </section>
      {indexAuxiliary && currentId && (
        <aside className="writing-detail module" role="region" aria-label="本章信息">
          {chapter?.index ? <ChapterMarginIndex
            index={chapter.index}
            variant="panel"
            activeItemId={writingPage?.informationId ?? null}
            onLocate={(item) => selectInformation(item.item_id)}
            onConfirm={(item) => {
              if (currentId && chapterId && item.candidate_id) {
                void useWritingStore.getState().confirmCandidate(currentId, chapterId, item.candidate_id);
              }
            }}
          /> : null}
        </aside>
      )}
      {currentId && <>
        {chat && !compact && <ResizeHandle value={plan.chat} min={CHAT.min} max={Math.min(CHAT.max, width - plan.sidebar - plan.paperMin - (sidebar ? 12 : 6))}
          invert label="对话栏宽度" onChange={prefs.setChatWidth} onReset={() => prefs.setChatWidth(CHAT.default)} />}
        <div id="writing-chat" className="writing-chat module flex min-h-0 flex-col" hidden={!chat} style={{ width: compact ? "100%" : plan.chat }}>
          <ConversationPanel onBackToDetail={undefined} />
        </div>
      </>}
    </div>
  </div>;
}
