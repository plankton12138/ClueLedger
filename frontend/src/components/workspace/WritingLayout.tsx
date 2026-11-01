import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { List, MessageSquare } from "lucide-react";
import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { EMPTY_CONTEXT } from "../../api/workspace";
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
import StorySurface from "../story/StorySurface";
import ClueSurface from "../clues/ClueSurface";
import WorkspaceDetail from "./WorkspaceDetail";
import { useStoryStore } from "../../state/story";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, fileLoading, openingPath, ready } = useWorkspaceStore();
  const page = useWorkspacePage();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const source = useSourceLocationStore(state => state.workspaceId === currentId ? state.location : null);
  const sourceId = useSourceLocationStore(state => state.workspaceId === currentId ? state.evidenceId : null);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId === currentId ? state.assembly : null);
  const moduleBinding = selectedModule?.workspaceId === currentId ? moduleAssembly?.bindings.find(binding => binding.id === selectedModule.bindingId) : undefined;
  const [width, setWidth] = useState(0);
  const mode = page?.kind === "writing" && page.workspaceId === currentId ? page.mode ?? "prose" : "prose";
  const storyView = mode === "story";
  const cluesView = mode === "clues";
  const storyNodeId = useStoryStore(state => state.nodeId);
  const storyClueId = useStoryStore(state => state.clueId);
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
  const capacity = writingLayout(width, { ...prefs, sidebarVisible: true, chatVisible: true });
  const canDockDirectory = capacity.sidebar > 0;
  const compact = capacity.kind === "single";
  const chat = Boolean(currentId && (compact ? compactChat : prefs.chatVisible));
  useLayoutEffect(() => {
    if (chat && prefs.chatReveal > 0) container.current?.querySelector<HTMLTextAreaElement>(".chat-input")?.focus();
  }, [chat, prefs.chatReveal]);
  const plan = writingLayout(width, { ...prefs, chatVisible: chat });
  const sidebar = plan.sidebar > 0;
  const directoryVisible = canDockDirectory ? sidebar : directory;
  const paper = !compact || !chat;
  const detailOpen = Boolean(currentId) && (
    (storyView && Boolean(storyNodeId)) ||
    Boolean(storyClueId && (cluesView || !storyView))
  );
  useEffect(() => {
    // 会话受理仍按构思页判断用途；路由模式是唯一来源。
    useStoryStore.getState().setOpen(storyView);
  }, [storyView]);
  useLayoutEffect(() => {
    if (previousWorkspace.current && previousWorkspace.current !== currentId) {
      setDirectory(false);
      setCompactChat(false);
      useStoryStore.getState().selectNode(null);
      useStoryStore.getState().selectClue(null);
    }
    previousWorkspace.current = currentId;
  }, [currentId]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);
  // 路由是选中状态的来源：直接打开链接、前进后退与刷新都回到同一个对象。
  useEffect(() => {
    if (!page || page.kind !== "writing" || page.workspaceId !== currentId) return;
    const store = useStoryStore.getState();
    const nextNode = page.mode === "story" ? page.nodeId ?? null : null;
    const nextClue = page.clueId ?? null;
    if (store.nodeId !== nextNode) store.selectNode(nextNode);
    if (store.clueId !== nextClue) store.selectClue(nextClue);
  }, [page, currentId]);
  useEffect(() => {
    if (!storyView && !cluesView) return;
    const sourceState = useSourceLocationStore.getState();
    if (sourceState.evidenceId || sourceState.storyReturn || sourceState.location) sourceState.clear();
  }, [storyView, cluesView]);
  useEffect(() => { if (prefs.chatReveal > 0) setCompactChat(true); }, [prefs.chatReveal]);
  useEffect(() => { if (sourceId) setCompactChat(false); }, [sourceId]);
  useEffect(() => {
    if (cluesView && currentId) void useStoryStore.getState().loadRelations(currentId, storyClueId);
  }, [cluesView, currentId, storyClueId]);
  function toggleSidebar() {
    if (canDockDirectory) prefs.toggleSidebar(); else setDirectory(value => !value);
  }
  function toggleChat() {
    if (compact) {
      setCompactChat(value => !value);
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
  function selectNode(nodeId: string | null) {
    if (!currentId) return;
    useStoryStore.getState().selectNode(nodeId);
    navigate({ kind: "writing", workspaceId: currentId, mode: "story", nodeId: nodeId ?? undefined }, true);
  }
  function selectClue(clueId: string | null) {
    if (!currentId) return;
    useStoryStore.getState().selectClue(clueId);
    navigate(
      {
        kind: "writing",
        workspaceId: currentId,
        mode: cluesView ? "clues" : undefined,
        clueId: clueId ?? undefined,
      },
      true,
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
      "根据正文校准当前构思：逐章读取现有正文，抽取能核对的事件、实际时间、线索出现位置和角色知情，并注明章节与原文引文。与当前构思逐项对照，只修改或补充有明显依据的缺口；作者真相未在正文明示时保持未定，逐项说明依据。节点在正文里有对应段落的，提交 bind_prose（node_id、path、quote 用正文原句），让构思与正文建立关联；没有对应段落的节点不要绑定。",
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
  function openClueFromProse(clueId: string) {
    selectClue(clueId);
  }
  function locateProse(target: { path: string; evidence_id?: string; chapter?: number }) {
    if (!currentId) return;
    if (target.evidence_id) {
      // 账本证据交给现有来源阅读；返回时回到刚才那一页。
      const taskChapter = file?.chapter_number ?? 0;
      useSourceLocationStore.setState({ storyReturn: true });
      navigate({
        kind: "writing",
        workspaceId: currentId,
        sourceId: target.evidence_id,
        asOfChapter: file?.chapter_number ?? undefined,
        taskChapter,
      });
      return;
    }
    navigate({ kind: "writing", workspaceId: currentId });
    void useWorkspaceStore.getState().openPath(target.path);
  }
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
      <aside id="writing-sidebar" role="region" aria-label="目录" className="writing-toc module" data-overlay={!canDockDirectory} hidden={!directoryVisible}
        style={{ width: sidebar ? plan.sidebar : Math.max(prefs.sidebarWidth, SIDEBAR.min * Math.max(1, prefs.fontScale / 100)) }}
        onKeyDown={event => { if (!canDockDirectory && event.key === "Escape") { event.stopPropagation(); setDirectory(false); } }}>
        <div className="writing-side-body">
          <WorkspaceToc onChoose={() => {
            const pending = useSourceLocationStore.getState().restorePosition;
            if (pending) useSourceLocationStore.getState().consumeRestore(pending.epoch);
            const sourceState = useSourceLocationStore.getState();
            if (sourceState.evidenceId || sourceState.storyReturn || (page?.kind === "writing" && page.sourceId)) sourceState.clear();
            if ((storyView || cluesView) && currentId) navigate({ kind: "writing", workspaceId: currentId });
            if (!canDockDirectory) { setDirectory(false); setCompactChat(false); }
          }} />
        </div>
      </aside>
      {sidebar && <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="侧栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} />}
      <section className="writing-paper module" role="region" aria-label={storyView ? "构思结构" : cluesView ? "全书线索" : "正文阅读"} hidden={!paper}>
        <div className="flex h-full min-h-0 flex-col"><SourceNotice /><div className="min-h-0 flex-1 flex flex-col">
        {currentId ? storyView ? (
          <StorySurface
            workspaceId={currentId}
            onStart={openStoryConversation}
            onCalibrate={() => void calibrateStoryFromProse()}
          />
        ) : cluesView ? (
          <ClueSurface
            workspaceId={currentId}
            selectedClueId={storyClueId}
            onSelectClue={selectClue}
          />
        ) : source?.source.kind === "module" ? <ModuleSourceReader /> : moduleBinding ? <WorkspaceModulePaper key={`${currentId}:${moduleBinding.id}`} workspaceId={currentId} binding={moduleBinding} /> : <Paper file={file} openingPath={openingPath} fileLoading={fileLoading} busy={loading} onOpenClue={openClueFromProse} /> : !ready ? <div className="ui-empty" role="status"><p>正在载入作品…</p></div> : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>}
        </div></div>
      </section>
      {detailOpen && currentId && (
        <aside className="writing-detail module" role="region" aria-label="详情">
          <WorkspaceDetail
            workspaceId={currentId}
            mode={storyView ? "story" : cluesView ? "clues" : "prose"}
            nodeId={storyNodeId}
            clueId={storyClueId}
            onClose={() => {
              if (storyView) selectNode(null);
              else selectClue(null);
            }}
            onLocateProse={locateProse}
          />
        </aside>
      )}
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
