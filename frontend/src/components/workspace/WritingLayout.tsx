import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Clock3, List, MessageSquare, Network, Search, Maximize2 } from "lucide-react";
import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/StatusMessage";
import WorkspaceToc from "./WorkspaceToc";
import ConversationPanel from "./ConversationPanel";
import ResizeHandle from "./ResizeHandle";
import Paper from "./Paper";
import WorkspaceModulePaper from "./WorkspaceModulePaper";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { SourceNotice, ModuleSourceReader } from "../clues/SourceReader";
import { navigate, parseHash } from "../../state/navigation";
import { useCluesStore, type CluePane } from "../../state/clues";
import ChapterClues from "../clues/ChapterClues";
import StorySurface from "../story/StorySurface";
import { useStoryStore } from "../../state/story";
import type { ClueReferenceInput } from "../../api/workspace";
import { sourceTaskChapter } from "../../state/sourceLocation";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, openDrawer } = useWorkspaceStore();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const source = useSourceLocationStore(state => state.workspaceId === currentId ? state.location : null);
  const sourceId = useSourceLocationStore(state => state.workspaceId === currentId ? state.evidenceId : null);
  const restoreEpoch = useSourceLocationStore(state => state.restoreEpoch);
  const pendingRestore = useSourceLocationStore(state => state.restorePosition);
  const pane = useCluesStore(state => currentId ? state.panes[currentId] ?? "chat" : "chat");
  const chapterContext = useCluesStore(state => currentId ? state.chapterPaths[currentId] : undefined);
  const catalog = useWorkspaceStore(state => state.catalog);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId===currentId ? state.assembly : null);
  const moduleBinding = selectedModule?.workspaceId===currentId ? moduleAssembly?.bindings.find(binding => binding.id===selectedModule.bindingId) : undefined;
  const chapterPath = !moduleBinding && (sourceId || file?.kind === "chapter") ? chapterContext ?? (file?.kind === "chapter" ? file.path : null) : null;
  const chapterNumber = catalog.chapters.find(item => item.path === chapterPath)?.chapter_number ?? null;
  const [width, setWidth] = useState(0);
  // 构思与正文共用一个纸面：开关放在 store 里，来源提示的「返回构思」也能打开它。
  // 只看开关本身：构思面挂载后自己按作品读取，作品切换时上面的一致性检查会复位。
  const storyView = useStoryStore(state => state.open);
  const setStoryView = useStoryStore(state => state.setOpen);
  const [storyDirectory, setStoryDirectory] = useState(false);
  const [directory, setDirectory] = useState(false);
  const [compactChat, setCompactChat] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const directoryButton = useRef<HTMLButtonElement>(null);
  const clueButton = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(entries[0].contentRect.width); });
    observer.observe(node); return () => observer.disconnect();
  }, []);
  const viewPrefs = { ...prefs, sidebarVisible: storyView ? storyDirectory : prefs.sidebarVisible };
  const capacity = writingLayout(width, { ...viewPrefs, sidebarVisible: true, chatVisible: true });
  const canDockDirectory = capacity.sidebar > 0;
  const compact = capacity.kind === "single";
  const chat = Boolean(currentId && (compact ? compactChat : prefs.chatVisible));
  useLayoutEffect(() => {
    if (chat && prefs.chatReveal > 0) container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [chat, prefs.chatReveal]);
  const plan = writingLayout(width, { ...viewPrefs, chatVisible: chat });
  const sidebar = plan.sidebar > 0;
  const directoryVisible = canDockDirectory ? sidebar : directory;
  const paper = !compact || !chat;
  useEffect(() => { setDirectory(false); setCompactChat(false); setStoryView(false); }, [currentId, setStoryView]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);
  useEffect(() => { if (prefs.chatReveal > 0) { setCompactChat(true); if (currentId) useCluesStore.getState().setPane(currentId, "chat"); } }, [prefs.chatReveal]);
  useEffect(() => { if (sourceId) setCompactChat(false); }, [sourceId]);
  useEffect(() => { if (restoreEpoch > 0 && pane === "clues") setCompactChat(true); }, [restoreEpoch]);
  useEffect(() => {
    if (currentId && !sourceId && !moduleBinding && file?.kind === "chapter" && !pendingRestore?.key.startsWith(`${currentId}:paper:`)) useCluesStore.getState().setChapter(currentId, file.path);
  }, [currentId, sourceId, file?.path, Boolean(moduleBinding), pendingRestore]);
  function showPane(next: CluePane, toggle = false) {
    if (!currentId) return;
    if (toggle && chat && pane === next) { setCompactChat(false); if (!compact) prefs.toggleChat(); return; }
    useCluesStore.getState().setPane(currentId, next);
    setCompactChat(true);
    if (!compact && !prefs.chatVisible) prefs.toggleChat();
  }
  function openStoryConversation() {
    if (!currentId) return;
    useCluesStore.getState().setPane(currentId, "chat");
    setCompactChat(true);
    prefs.revealChat();
  }
  function locate(evidenceId: string, asOf: number) {
    if (!currentId) return;
    const taskChapter = sourceTaskChapter(currentId) ?? file?.chapter_number ?? 0;
    useSourceLocationStore.getState().capture({ kind: "writing", workspaceId: currentId }, taskChapter);
    navigate({ kind: "writing", workspaceId: currentId, sourceId: evidenceId, asOfChapter: asOf, taskChapter });
  }
  // 带上线索只写进消息草稿，不发送；引用版本在发送时才由服务端核对。
  async function referenceClue(reference: ClueReferenceInput, title: string) {
    if (!currentId) return;
    const store = useConversationsStore.getState();
    if (store.workspaceId !== currentId) await store.load(currentId);
    if (!useConversationsStore.getState().addClueReference(reference, title)) return;
    useCluesStore.getState().setPane(currentId, "chat");
    setCompactChat(true);
    if (!compact && !prefs.chatVisible) prefs.toggleChat();
  }
  const chatLabel = running ? "对话 · 进行中" : "对话";
  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      <Button ref={directoryButton} size="compact" variant={directoryVisible ? "primary" : "secondary"} aria-pressed={directoryVisible} aria-expanded={directoryVisible} aria-controls="writing-directory"
        onClick={() => canDockDirectory ? storyView ? setStoryDirectory(value => !value) : prefs.toggleSidebar() : setDirectory(value => !value)} onKeyDown={event => { if (event.key === "Escape") setDirectory(false); }}>
        <List size={16} strokeWidth={2} aria-hidden="true" />目录</Button>
      <Button size="compact" variant={storyView ? "primary" : "secondary"} aria-pressed={storyView} onClick={() => setStoryView(!storyView)} disabled={!currentId} disabledReason="先打开作品目录">
        <Network size={16} strokeWidth={2} aria-hidden="true" />构思</Button>
      <Button size="compact" variant={chat && pane === "chat" ? "primary" : "secondary"} aria-pressed={chat && pane === "chat"} aria-controls="writing-chat" onClick={() => showPane("chat", true)} disabled={!currentId} disabledReason="先打开作品目录">
        <MessageSquare size={16} strokeWidth={2} aria-hidden="true" />{chatLabel}</Button>
      <Button ref={clueButton} size="compact" variant={chat && pane === "clues" ? "primary" : "secondary"} aria-pressed={chat && pane === "clues"} aria-controls="writing-clues" onClick={() => showPane("clues", true)} disabled={!currentId} disabledReason="先打开作品目录"><Search size={16} aria-hidden="true" />本章线索</Button>
      {storyView && <Button size="compact" aria-pressed={!chat && !directoryVisible} onClick={() => { setStoryDirectory(false); setDirectory(false); if (chat) { setCompactChat(false); if (!compact) prefs.toggleChat(); } else { showPane("chat"); } }}><Maximize2 size={16} aria-hidden="true" />专注构思</Button>}
      <Button className="writing-activity" size="compact" disabled={!currentId} disabledReason="先打开作品目录" onClick={() => void openDrawer()}>
        <Clock3 size={16} strokeWidth={2} aria-hidden="true" />活动记录</Button>
    </div>
    <div className="writing-panes workbench">
      <aside id="writing-directory" role="region" aria-label="目录" className="writing-toc module" data-overlay={!canDockDirectory} hidden={!directoryVisible}
        style={{ width: sidebar ? plan.sidebar : Math.max(prefs.sidebarWidth, SIDEBAR.min * Math.max(1, prefs.fontScale / 100)) }}
        onKeyDown={event => { if (!canDockDirectory && event.key === "Escape") { event.stopPropagation(); setDirectory(false); directoryButton.current?.focus(); } }}>
        <WorkspaceToc onChoose={() => {
          const page = parseHash(window.location.hash);
          const pending = useSourceLocationStore.getState().restorePosition;
          if (pending) useSourceLocationStore.getState().consumeRestore(pending.epoch);
          if (page?.kind === "writing" && page.sourceId) { useSourceLocationStore.getState().clear(); navigate({ kind: "writing", workspaceId: page.workspaceId }); }
          if (!canDockDirectory) { setDirectory(false); setCompactChat(false); directoryButton.current?.focus(); }
        }} />
      </aside>
      {sidebar && <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="目录栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} />}
      <section className="writing-paper module" role="region" aria-label={storyView ? "构思结构" : "正文阅读"} hidden={!paper}>
        <div className="flex h-full min-h-0 flex-col"><SourceNotice /><div className="min-h-0 flex-1 flex flex-col">
        {currentId ? storyView ? (
          <StorySurface
            workspaceId={currentId}
            onStart={openStoryConversation}
            onReference={openStoryConversation}
            onLocate={binding => {
              // 从结构跳到正文：先回到正文面，再按结构里记的区间定位。
              setStoryView(false);
              void useSourceLocationStore.getState().showLocal(
                currentId,
                { path: binding.path, start: binding.start, end: binding.end, quote: binding.quote },
                { fromStory: true },
              );
            }}
          />
        ) : source?.source.kind === "module" ? <ModuleSourceReader /> : moduleBinding ? <WorkspaceModulePaper key={`${currentId}:${moduleBinding.id}`} workspaceId={currentId} binding={moduleBinding} /> : <Paper file={file} loading={loading} /> : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>}
        </div></div>
      </section>
      {currentId && <>
        {chat && !compact && <ResizeHandle value={plan.chat} min={CHAT.min} max={Math.min(CHAT.max, width - plan.sidebar - plan.paperMin - (sidebar ? 12 : 6))}
          invert label="对话栏宽度" onChange={prefs.setChatWidth} onReset={() => prefs.setChatWidth(CHAT.default)} />}
        <div className="writing-chat module flex min-h-0 flex-col" hidden={!chat} style={{ width: compact ? "100%" : plan.chat }} onKeyDown={event => {
          if (event.key === "Escape" && pane === "clues" && compact) { event.stopPropagation(); setCompactChat(false); clueButton.current?.focus(); }
        }}>
          <div role="tablist" aria-label="写作辅助" className="writing-assistant-tabs" onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault(); const next = event.key === "Home" ? "chat" : event.key === "End" ? "clues" : pane === "chat" ? "clues" : "chat";
            showPane(next); event.currentTarget.querySelector<HTMLButtonElement>(`[data-pane="${next}"]`)?.focus();
          }}>
            <button id="assistant-chat-tab" role="tab" data-pane="chat" aria-selected={pane === "chat"} aria-controls="writing-chat" tabIndex={pane === "chat" ? 0 : -1} className="ui-button" onClick={() => showPane("chat")}>对话</button>
            <button id="assistant-clues-tab" role="tab" data-pane="clues" aria-selected={pane === "clues"} aria-controls="writing-clues" tabIndex={pane === "clues" ? 0 : -1} className="ui-button" onClick={() => showPane("clues")}>本章线索</button>
          </div>
          <div id="writing-chat" role="tabpanel" aria-labelledby="assistant-chat-tab" hidden={pane !== "chat"} className="min-h-0 flex-1"><ConversationPanel /></div>
          <div id="writing-clues" role="tabpanel" aria-labelledby="assistant-clues-tab" hidden={pane !== "clues"} className="min-h-0 flex-1"><ChapterClues workspaceId={currentId} chapterPath={chapterPath} asOf={chapterNumber} active={chat && pane === "clues"} onLocate={locate} onReference={referenceClue} /></div>
        </div>
      </>}
    </div>
  </div>;
}
