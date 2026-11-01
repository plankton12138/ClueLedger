import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { List, MessageSquare } from "lucide-react";
import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { EMPTY_CONTEXT, type ClueReferenceInput } from "../../api/workspace";
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
import { navigate, useWorkspacePage } from "../../state/navigation";
import { useCluesStore } from "../../state/clues";
import ChapterClues from "../clues/ChapterClues";
import StoryClues from "../story/StoryClues";
import StorySurface from "../story/StorySurface";
import { useStoryStore } from "../../state/story";
import { sourceTaskChapter } from "../../state/sourceLocation";

type SidebarTab = "directory" | "chapter" | "story";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, fileLoading, openingPath, ready } = useWorkspaceStore();
  const page = useWorkspacePage();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const source = useSourceLocationStore(state => state.workspaceId === currentId ? state.location : null);
  const sourceId = useSourceLocationStore(state => state.workspaceId === currentId ? state.evidenceId : null);
  const chapterContext = useCluesStore(state => currentId ? state.chapterPaths[currentId] : undefined);
  const catalog = useWorkspaceStore(state => state.catalog);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId===currentId ? state.assembly : null);
  const moduleBinding = selectedModule?.workspaceId===currentId ? moduleAssembly?.bindings.find(binding => binding.id===selectedModule.bindingId) : undefined;
  const chapterPath = !moduleBinding && (sourceId || file?.kind === "chapter") ? chapterContext ?? (file?.kind === "chapter" ? file.path : null) : null;
  const chapterNumber = catalog.chapters.find(item => item.path === chapterPath)?.chapter_number ?? null;
  const [width, setWidth] = useState(0);
  const storyView = page?.kind === "writing" && page.workspaceId === currentId && page.mode === "story";
  const storyGraph = useStoryStore(state => state.graph);
  const storyVariantId = useStoryStore(state => state.variantId);
  const storyNodeId = useStoryStore(state => state.nodeId);
  const storyClueId = useStoryStore(state => state.clueId);
  const storyLoading = useStoryStore(state => state.loading);
  const storyError = useStoryStore(state => state.error);
  const selectStoryClue = useStoryStore(state => state.selectClue);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("directory");
  const [directory, setDirectory] = useState(false);
  const [compactChat, setCompactChat] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const previousWorkspace = useRef<string | null>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(entries[0].contentRect.width); });
    observer.observe(node); return () => observer.disconnect();
  }, []);
  // 线索详情比目录需要更宽的阅读栏；不改写用户保存的目录宽度，只在本模式下临时加宽。
  const sidebarPreferred = sidebarTab === "story"
    ? Math.max(prefs.sidebarWidth, 320 * Math.max(1, prefs.fontScale / 100))
    : sidebarTab === "chapter"
      ? Math.max(prefs.sidebarWidth, 280 * Math.max(1, prefs.fontScale / 100))
      : prefs.sidebarWidth;
  const capacity = writingLayout(width, { ...prefs, sidebarWidth: sidebarPreferred, sidebarVisible: true, chatVisible: true });
  const canDockDirectory = capacity.sidebar > 0;
  const compact = capacity.kind === "single";
  const chat = Boolean(currentId && (compact ? compactChat : prefs.chatVisible));
  useLayoutEffect(() => {
    if (chat && prefs.chatReveal > 0) container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [chat, prefs.chatReveal]);
  const plan = writingLayout(width, { ...prefs, sidebarWidth: sidebarPreferred, chatVisible: chat });
  const sidebar = plan.sidebar > 0;
  const directoryVisible = canDockDirectory ? sidebar : directory;
  const paper = !compact || !chat;
  useEffect(() => {
    // 会话受理仍按构思页判断用途；路由模式是唯一来源，进入/离开构思时同步一次。
    useStoryStore.getState().setOpen(storyView);
  }, [storyView]);
  useLayoutEffect(() => {
    if (previousWorkspace.current && previousWorkspace.current !== currentId) {
      setDirectory(false);
      setCompactChat(false);
      setSidebarTab("directory");
    }
    previousWorkspace.current = currentId;
  }, [currentId]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);
  useEffect(() => { setSidebarTab(storyView ? "story" : "directory"); }, [storyView]);
  useEffect(() => {
    if (storyView && storyClueId) setSidebarTab("story");
  }, [storyClueId, storyView]);
  useEffect(() => {
    if (sidebarTab !== "story" || !currentId) return;
    const state = useStoryStore.getState();
    if (state.workspaceId !== currentId || !state.graph) void state.load(currentId);
  }, [sidebarTab, currentId]);
  useEffect(() => {
    if (!storyView) return;
    const sourceState = useSourceLocationStore.getState();
    if (sourceState.evidenceId || sourceState.storyReturn || sourceState.location) sourceState.clear();
  }, [storyView]);
  useEffect(() => { if (prefs.chatReveal > 0) setCompactChat(true); }, [prefs.chatReveal]);
  useEffect(() => { if (sourceId) setCompactChat(false); }, [sourceId]);
  useEffect(() => {
    const restore = useSourceLocationStore.getState().restorePosition;
    if (currentId && !sourceId && !moduleBinding && file?.kind === "chapter" && !(restore && restore.key.startsWith(`${currentId}:paper:`))) useCluesStore.getState().setChapter(currentId, file.path);
  }, [currentId, sourceId, file?.path, Boolean(moduleBinding)]);
  function toggleSidebar() {
    if (canDockDirectory) prefs.toggleSidebar(); else setDirectory((value) => !value);
  }
  function toggleChat() {
    if (compact) {
      setCompactChat((value) => !value);
      return;
    }
    if (prefs.chatVisible) prefs.toggleChat();
    else prefs.revealChat();
  }
  function openChat() {
    setCompactChat(true);
    prefs.revealChat();
  }
  function openStoryConversation() {
    openChat();
  }
  function locate(evidenceId: string, asOf: number) {
    if (!currentId) return;
    const taskChapter = sourceTaskChapter(currentId) ?? file?.chapter_number ?? 0;
    useSourceLocationStore.getState().capture({ kind: "writing", workspaceId: currentId }, taskChapter);
    navigate({ kind: "writing", workspaceId: currentId, sourceId: evidenceId, asOfChapter: asOf, taskChapter });
  }
  async function locateStoryEvidence(evidenceId: string, asOf: number) {
    if (!currentId) return;
    const taskChapter = sourceTaskChapter(currentId) ?? file?.chapter_number ?? 0;
    useSourceLocationStore.setState({ storyReturn: true });
    navigate({ kind: "writing", workspaceId: currentId, sourceId: evidenceId, asOfChapter: asOf, taskChapter });
  }
  function openStoryNode(nodeId: string) {
    if (!currentId) return;
    void useStoryStore.getState().selectNode(nodeId);
    navigate({ kind: "writing", workspaceId: currentId, mode: "story" });
  }
  // 带上线索只写进消息草稿，不发送；引用版本在发送时才由服务端核对。
  async function referenceClue(reference: ClueReferenceInput, title: string) {
    if (!currentId) return;
    const store = useConversationsStore.getState();
    if (store.workspaceId !== currentId) await store.load(currentId);
    if (!useConversationsStore.getState().addClueReference(reference, title)) return;
    openChat();
  }
  async function writeStoryNode(nodeId: string) {
    if (!currentId) return;
    const story = useStoryStore.getState();
    const graph = story.graph;
    const node = graph?.nodes.find(item => item.id === nodeId);
    if (!graph || !story.variantId || !node) return;
    const store = useConversationsStore.getState();
    if (store.workspaceId !== currentId) await store.load(currentId);
    navigate({ kind: "writing", workspaceId: currentId });
    openChat();
    await useConversationsStore.getState().send(
      `写这一段正文：${node.title}。按构思里的行动、变化和线索呈现来写，不要提前写出作者真相。`,
      {
        ...EMPTY_CONTEXT,
        story: {
          variant_id: story.variantId,
          revision: graph.revision,
          node_ids: [node.id],
          purpose: "write",
        },
      },
      { purpose: "chat", preserveDraft: true },
    );
  }
  async function calibrateStoryFromProse() {
    if (!currentId) return;
    const story = useStoryStore.getState();
    if (!story.variantId || !story.graph) return;
    const store = useConversationsStore.getState();
    if (store.workspaceId !== currentId) await store.load(currentId);
    openChat();
    await useConversationsStore.getState().send(
      "根据正文校准当前构思：逐章读取现有正文，抽取能核对的事件、实际时间、线索出现位置和角色知情，并注明章节与原文引文。与当前构思逐项对照，只修改或补充有明显依据的缺口；作者真相未在正文明示时保持未定，逐项说明依据。",
      {
        ...EMPTY_CONTEXT,
        story: {
          variant_id: story.variantId,
          revision: story.graph.revision,
          node_ids: [],
          purpose: "design",
        },
      },
      { purpose: "chat", preserveDraft: true },
    );
  }
  const tabs: { id: SidebarTab; label: string }[] = storyView
    ? [{ id: "directory", label: "目录" }, { id: "story", label: "全书线索" }]
    : [{ id: "directory", label: "目录" }, { id: "chapter", label: "本章线索" }, { id: "story", label: "全书线索" }];
  const chatLabel = running ? "对话 · 进行中" : "对话";
  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      <Button size="compact" variant={directoryVisible ? "primary" : "secondary"} aria-pressed={directoryVisible} aria-expanded={directoryVisible} aria-controls="writing-sidebar"
        onClick={toggleSidebar} disabled={!currentId} disabledReason="先打开作品目录">
        <List size={16} strokeWidth={2} aria-hidden="true" />侧栏
      </Button>
      <Button size="compact" variant={chat ? "primary" : "secondary"} aria-pressed={chat} aria-controls="writing-chat"
        onClick={toggleChat} disabled={!currentId} disabledReason="先打开作品目录">
        <MessageSquare size={16} strokeWidth={2} aria-hidden="true" />{chatLabel}
      </Button>
    </div>
    <div className="writing-panes workbench">
      <aside id="writing-sidebar" role="region" aria-label={sidebarTab === "directory" ? "目录" : sidebarTab === "chapter" ? "本章线索" : "全书线索"} className="writing-toc module" data-overlay={!canDockDirectory} hidden={!directoryVisible}
        style={{ width: sidebar ? plan.sidebar : Math.max(sidebarPreferred, SIDEBAR.min * Math.max(1, prefs.fontScale / 100)) }}
        onKeyDown={event => { if (!canDockDirectory && event.key === "Escape") { event.stopPropagation(); setDirectory(false); } }}>
        <div className="writing-side-tabs" role="tablist" aria-label="侧栏内容" onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const index = tabs.findIndex(tab => tab.id === sidebarTab);
          const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1]
            : tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
          setSidebarTab(next.id);
          event.currentTarget.querySelector<HTMLButtonElement>(`[data-side-tab="${next.id}"]`)?.focus();
        }}>
          {tabs.map(tab => <button key={tab.id} type="button" role="tab" data-side-tab={tab.id} aria-selected={sidebarTab === tab.id}
            tabIndex={sidebarTab === tab.id ? 0 : -1} className="writing-side-tab" onClick={() => setSidebarTab(tab.id)}>{tab.label}</button>)}
        </div>
        <div className="writing-side-body">
          {sidebarTab === "directory" ? <WorkspaceToc onChoose={() => {
            const pending = useSourceLocationStore.getState().restorePosition;
            if (pending) useSourceLocationStore.getState().consumeRestore(pending.epoch);
            const sourceState = useSourceLocationStore.getState();
            if (sourceState.evidenceId || sourceState.storyReturn || (page?.kind === "writing" && page.sourceId)) sourceState.clear();
            if (storyView && currentId) navigate({ kind: "writing", workspaceId: currentId });
            if (!canDockDirectory) { setDirectory(false); setCompactChat(false); }
          }} /> : sidebarTab === "chapter" ? (
            <ChapterClues workspaceId={currentId ?? ""} chapterPath={chapterPath} asOf={chapterNumber} active={directoryVisible}
              onLocate={locate} onReference={referenceClue} />
          ) : storyVariantId ? (
            <StoryClues
              workspaceId={currentId ?? ""}
              variantId={storyVariantId}
              nodeId={storyNodeId}
              asOf={null}
              active={directoryVisible}
              selectedClueId={storyClueId}
              onSelectClue={selectStoryClue}
              nodeTitle={id => storyGraph?.nodes.find(node => node.id === id)?.title ?? id}
              onLocateNode={openStoryNode}
              onLocateEvidence={(id, asOf) => void locateStoryEvidence(id, asOf)}
              onReference={referenceClue}
              onClearNodeFilter={() => void useStoryStore.getState().selectNode(null)}
            />
          ) : (
            <div className="writing-side-empty">
              <p>{storyLoading ? "正在读取构思…" : storyError ?? "还没有构思。切到构思模式建立结构与线索。"}</p>
            </div>
          )}
        </div>
      </aside>
      {sidebar && <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="侧栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} />}
      <section className="writing-paper module" role="region" aria-label={storyView ? "构思结构" : "正文阅读"} hidden={!paper}>
        <div className="flex h-full min-h-0 flex-col"><SourceNotice /><div className="min-h-0 flex-1 flex flex-col">
        {currentId ? storyView ? (
          <StorySurface
            workspaceId={currentId}
            onStart={openStoryConversation}
            onReference={openStoryConversation}
            onWriteScene={(nodeId) => void writeStoryNode(nodeId)}
            onCalibrate={() => void calibrateStoryFromProse()}
            onLocate={binding => {
              useSourceLocationStore.setState({ storyReturn: true });
              navigate({ kind: "writing", workspaceId: currentId });
              void useSourceLocationStore.getState().showLocal(
                currentId,
                { path: binding.path, start: binding.start, end: binding.end, quote: binding.quote },
                { fromStory: true },
              );
            }}
          />
        ) : source?.source.kind === "module" ? <ModuleSourceReader /> : moduleBinding ? <WorkspaceModulePaper key={`${currentId}:${moduleBinding.id}`} workspaceId={currentId} binding={moduleBinding} /> : <Paper file={file} openingPath={openingPath} fileLoading={fileLoading} busy={loading} /> : !ready ? <div className="ui-empty" role="status"><p>正在载入作品…</p></div> : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>}
        </div></div>
      </section>
      {currentId && <>
        {chat && !compact && <ResizeHandle value={plan.chat} min={CHAT.min} max={Math.min(CHAT.max, width - plan.sidebar - plan.paperMin - (sidebar ? 12 : 6))}
          invert label="对话栏宽度" onChange={prefs.setChatWidth} onReset={() => prefs.setChatWidth(CHAT.default)} />}
        <div id="writing-chat" className="writing-chat module flex min-h-0 flex-col" hidden={!chat} style={{ width: compact ? "100%" : plan.chat }}>
          <ConversationPanel />
        </div>
      </>}
    </div>
  </div>;
}
