/**
 * 单一写作工作区（v1.2）。
 *
 * 左侧是全书结构与章节，中部只读展示结构、正文或模块；对话是唯一创作修改入口。
 * 窄屏时对话与稿纸切换显示，宽屏时对话持续可见。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { List, MessageSquare, PanelRight, Tag } from "lucide-react";

import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { draftKeyOf, runningConversation, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { BookClues, ChapterClues } from "../clues/CluePanel";
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
import StructureView from "../writing/StructureView";
import ChapterMarginIndex from "../writing/ChapterMarginIndex";
import { useWritingStore } from "../../state/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import EmptyWritingGuide from "../writing/EmptyWritingGuide";
import { useRevisionPanelStore } from "../../state/revisions";

const MODULE_REQUEST = "帮我判断这个故事还需要哪些长期资料，比如人物小传、故事背景；需要的话请创建并装入。";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, fileLoading, openingPath, ready } = useWorkspaceStore();
  const page = useWorkspacePage();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const source = useSourceLocationStore(state => state.workspaceId === currentId ? state.location : null);
  const sourceId = useSourceLocationStore(state => state.workspaceId === currentId ? state.evidenceId : null);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId === currentId ? state.assembly : null);
  const writingPage = page?.kind === "writing" && page.workspaceId === currentId ? page : null;
  const chapterId = writingPage?.chapterId ?? null;
  const prosePath = writingPage?.prosePath ?? null;
  // 模块内容是全书页的一种阅读态：地址栏选了章节或正文，阅读区就按章节与正文走。
  const moduleBinding = !chapterId && !prosePath && selectedModule?.workspaceId === currentId
    ? moduleAssembly?.bindings.find(binding => binding.id === selectedModule.bindingId)
    : undefined;
  const workbench = useWritingStore(state => state.workspaceId === currentId ? state.workbench : null);
  const writingLoading = useWritingStore(state => state.workspaceId === currentId ? state.loading : false);
  const writingError = useWritingStore(state => state.workspaceId === currentId ? state.error : null);
  const loadWorkbench = useWritingStore(state => state.load);
  const refreshWorkbench = useWritingStore(state => state.refresh);
  const chapter = chapterId && workbench?.chapter?.plan?.id === chapterId ? workbench.chapter : null;
  const book = workbench?.book ?? null;
  const [width, setWidth] = useState(0);
  const [directory, setDirectory] = useState(false);
  const [compactChat, setCompactChat] = useState(false);
  const [indexOpen, setIndexOpen] = useState(false);
  const [clueOpen, setClueOpen] = useState(false);
  const [editingChapter, setEditingChapter] = useState<string | null>(null);
  const closeRevisionPanel = useRevisionPanelStore(state => state.close);
  const container = useRef<HTMLDivElement>(null);
  const previousWorkspace = useRef<string | null>(null);
  const focusedEmptyWorkspace = useRef<string | null>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(entries[0].contentRect.width); });
    observer.observe(node); return () => observer.disconnect();
  }, []);
  const capacity = writingLayout(width, { ...prefs, sidebarVisible: true, chatVisible: true });
  const canDockDirectory = capacity.sidebar > 0;
  const compact = capacity.kind === "single";
  const chat = Boolean(currentId && (compact ? compactChat : true));
  const plan = writingLayout(width, { ...prefs, chatVisible: chat });
  const sidebar = plan.sidebar > 0;
  const directoryVisible = canDockDirectory ? sidebar : directory;
  const hasIndex = Boolean(chapter?.index);
  const chapterClues = chapter?.clues ?? null;
  const hasClues = Boolean(chapterClues && chapterClues.entries.length > 0);
  const hasDetail = hasIndex || hasClues;
  const expandedPaperWidth = width - (sidebar ? plan.sidebar + 6 : 0);
  const inlineIndexAvailable = Boolean(hasIndex && !compact && expandedPaperWidth >= 980);
  const indexAuxiliary = Boolean(indexOpen && hasDetail && !chat && !inlineIndexAvailable);
  const auxiliaryVisible = chat || indexAuxiliary;
  const paperVisible = !compact || !auxiliaryVisible;

  useEffect(() => {
    if (!currentId) return;
    void loadWorkbench(currentId, { chapterId, prosePath });
  }, [currentId, chapterId, prosePath, loadWorkbench]);
  useEffect(() => {
    if (prosePath) void useWorkspaceStore.getState().openPath(prosePath);
  }, [prosePath]);
  useEffect(() => {
    if (chapter?.prose?.path) void useWorkspaceStore.getState().openPath(chapter.prose.path);
  }, [chapter?.prose?.path]);
  useEffect(() => {
    if (compact && currentId && book && book.chapters.length === 0 && !book.structure_exists) {
      setCompactChat(true);
    }
  }, [book, compact, currentId]);
  useEffect(() => {
    setIndexOpen(false);
    setEditingChapter(null);
    closeRevisionPanel();
  }, [currentId, chapterId, closeRevisionPanel]);
  useEffect(() => {
    if (inlineIndexAvailable) setIndexOpen(false);
  }, [inlineIndexAvailable]);
  useEffect(() => { if (prefs.chatReveal > 0) setCompactChat(true); }, [prefs.chatReveal]);
  useEffect(() => { if (sourceId) setCompactChat(false); }, [sourceId]);
  useLayoutEffect(() => {
    if (chat && !indexAuxiliary && prefs.chatReveal > 0) container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [chat, indexAuxiliary, prefs.chatReveal]);
  useLayoutEffect(() => {
    if (
      !chat || !currentId || !book || book.chapters.length > 0 || book.structure_exists
      || focusedEmptyWorkspace.current === currentId
    ) return;
    focusedEmptyWorkspace.current = currentId;
    container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [book, chat, currentId]);
  useLayoutEffect(() => {
    if (previousWorkspace.current && previousWorkspace.current !== currentId) {
      setDirectory(false);
      setCompactChat(false);
      setIndexOpen(false);
    }
    previousWorkspace.current = currentId;
  }, [currentId]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);

  function selectStructure() {
    if (!currentId) return;
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, prosePath: STRUCTURE_PATH });
  }
  function selectChapter(id: string) {
    if (!currentId) return;
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, chapterId: id });
  }
  function selectProse(path: string) {
    if (!currentId) return;
    leaveModule();
    navigate({ kind: "writing", workspaceId: currentId, prosePath: path });
  }
  /** 模块内容是全书页上的阅读态：作者选了章节或正文，就退出它。 */
  function leaveModule() {
    if (useWorkspaceModulesStore.getState().selected) useWorkspaceModulesStore.getState().close();
  }
  function closeChat() {
    if (compact) setCompactChat(false);
  }
  function toggleChat() {
    if (!compact) return;
    setCompactChat(value => {
      if (!value) setIndexOpen(false);
      return !value;
    });
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
  function selectInformation(itemId: string) {
    if (!currentId || !chapterId) return;
    if (compact) setIndexOpen(false);
    navigate(
      { kind: "writing", workspaceId: currentId, chapterId, informationId: itemId },
      true,
    );
  }
  function prefillChat(text: string) {
    const conversations = useConversationsStore.getState();
    const currentDraft = conversations.drafts[draftKeyOf(conversations)] ?? "";
    if (!currentDraft.trim()) conversations.setDraft(text);
    if (compact) setCompactChat(true);
    requestAnimationFrame(() => {
      container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
    });
  }
  function chooseStarter(text: string) {
    prefillChat(text);
  }
  function askForModules() {
    prefillChat(MODULE_REQUEST);
    // 窄屏时侧栏是覆盖层，填完草稿就退回对话并收起覆盖层。
    if (!canDockDirectory) setDirectory(false);
  }
  /** 结构尚未建立时，把作者带到对话；第一版结构必须由模型写入。 */
  function askForStructure() {
    prefillChat("先帮我建立这本书的全书结构，再开始写正文。");
    if (compact) setCompactChat(true);
    else if (!useLayoutStore.getState().chatVisible) useLayoutStore.getState().revealChat();
    if (!canDockDirectory) setDirectory(false);
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
        return <Paper file={file} openingPath={chapter.prose.path} fileLoading={fileLoading} busy={loading}
          chapterId={file?.path === chapter.prose.path ? chapterId : null}
          onRevisionExit={editingChapter === chapterId ? () => {
            setEditingChapter(null);
            if (compact) setCompactChat(true);
          } : undefined} />;
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
      return <Paper file={file} openingPath={openingPath} fileLoading={fileLoading} busy={loading} />;
    }
    if (book && workbench) return <article className="reader-document" aria-label="稿纸">
      <div className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm">
        <div className="paper-lined min-h-full">
          {book.chapters.length === 0
            ? <EmptyWritingGuide structureReady={book.structure_exists} onChoose={chooseStarter} />
            : <p className="mx-auto max-w-2xl text-center text-sm text-mute">选择一章阅读</p>}
        </div>
      </div>
    </article>;
    if (writingError) return <div className="writing-load-error"><StatusMessage tone="error" action={<Button size="compact" onClick={() => void refreshWorkbench(currentId, {})}>重试</Button>}>{writingError}</StatusMessage></div>;
    return <div className="ui-empty" role="status"><p>{writingLoading || loading ? "正在读取写作页…" : "还没有写作内容。"}</p></div>;
  }

  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      <Button size="compact" variant={directoryVisible ? "primary" : "secondary"} aria-pressed={directoryVisible} aria-expanded={directoryVisible} aria-controls="writing-sidebar"
        onClick={toggleSidebar} disabled={!currentId} disabledReason="先打开作品目录">
        <List size={16} strokeWidth={2} aria-hidden="true" />侧栏
      </Button>
      {chapterId && hasDetail && !inlineIndexAvailable && <Button size="compact" variant={indexAuxiliary ? "primary" : "secondary"} aria-pressed={indexAuxiliary}
        onClick={toggleIndex} disabled={!hasDetail} disabledReason="这一章还没有信息或线索">
        <PanelRight size={16} strokeWidth={2} aria-hidden="true" />本章信息
      </Button>}
      <AppDialog open={clueOpen} onOpenChange={setClueOpen} title="线索" className="clue-dialog"
        trigger={<Button size="compact" disabled={!currentId} disabledReason="先打开作品目录">
          <Tag size={16} strokeWidth={2} aria-hidden="true" />线索
        </Button>}>
        {chapterClues && <div className="chapter-margin clue-chapter">
          <ChapterClues clues={chapterClues} heading={`第 ${chapterClues.chapter} 章线索`} />
        </div>}
        <div className="chapter-margin clue-book">
          <BookClues clues={book?.clues ?? { entries: [], plans: [], findings: [] }} />
        </div>
      </AppDialog>
      {compact && <Button size="compact" variant={chat ? "primary" : "secondary"} aria-pressed={chat} aria-controls="writing-chat"
        onClick={toggleChat} disabled={!currentId} disabledReason="先打开作品目录">
        <MessageSquare size={16} strokeWidth={2} aria-hidden="true" />{chatLabel}
      </Button>}
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
            onStructure={selectStructure}
            onChapter={selectChapter}
            onProse={selectProse}
            onAskModules={askForModules}
            onAskStructure={askForStructure}
            planRevision={workbench?.plan_revision ?? null}
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
          {chapter?.index || chapterClues ? <ChapterMarginIndex
            index={chapter?.index ?? null}
            clues={chapterClues}
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
          <ConversationPanel
            onRevise={chapterId && chapter?.prose && file?.path === chapter.prose.path && editingChapter !== chapterId ? () => {
              setEditingChapter(chapterId);
              if (compact) setCompactChat(false);
            } : undefined}
          />
        </div>
      </>}
    </div>
  </div>;
}
