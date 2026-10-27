import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Clock3, List, MessageSquare } from "lucide-react";
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

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, openDrawer } = useWorkspaceStore();
  const running = useConversationsStore(runningConversation);
  const selectedModule = useWorkspaceModulesStore(state => state.selected);
  const moduleAssembly = useModuleLibraryStore(state => state.workspaceId===currentId ? state.assembly : null);
  const moduleBinding = selectedModule?.workspaceId===currentId ? moduleAssembly?.bindings.find(binding => binding.id===selectedModule.bindingId) : undefined;
  const [width, setWidth] = useState(0);
  const [directory, setDirectory] = useState(false);
  const [compactChat, setCompactChat] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const directoryButton = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(Math.max(0, entries[0].contentRect.width - 24)); });
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
  useEffect(() => { setDirectory(false); setCompactChat(false); }, [currentId]);
  useEffect(() => { setDirectory(false); }, [canDockDirectory]);
  useEffect(() => { if (prefs.chatReveal > 0) setCompactChat(true); }, [prefs.chatReveal]);
  function toggleChat() {
    if (compact) setCompactChat(value => !value);
    else prefs.toggleChat();
  }
  const chatLabel = running ? "对话 · 进行中" : "对话";
  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      <Button ref={directoryButton} size="compact" variant={directoryVisible ? "primary" : "secondary"} aria-pressed={directoryVisible} aria-expanded={directoryVisible} aria-controls="writing-directory"
        onClick={() => canDockDirectory ? prefs.toggleSidebar() : setDirectory(value => !value)} onKeyDown={event => { if (event.key === "Escape") setDirectory(false); }}>
        <List size={16} strokeWidth={2} aria-hidden="true" />目录</Button>
      <Button size="compact" variant={chat ? "primary" : "secondary"} aria-pressed={chat} aria-controls="writing-chat" onClick={toggleChat} disabled={!currentId} disabledReason="先打开作品目录">
        <MessageSquare size={16} strokeWidth={2} aria-hidden="true" />{chatLabel}</Button>
      <Button size="compact" disabled={!currentId} disabledReason="先打开作品目录" onClick={() => void openDrawer()}>
        <Clock3 size={16} strokeWidth={2} aria-hidden="true" />活动记录</Button>
    </div>
    <div className="writing-panes workbench">
      <aside id="writing-directory" role="region" aria-label="目录" className="writing-toc module" data-overlay={!canDockDirectory} hidden={!directoryVisible}
        style={{ width: sidebar ? plan.sidebar : Math.max(prefs.sidebarWidth, SIDEBAR.min * Math.max(1, prefs.fontScale / 100)) }}
        onKeyDown={event => { if (!canDockDirectory && event.key === "Escape") { event.stopPropagation(); setDirectory(false); directoryButton.current?.focus(); } }}>
        <WorkspaceToc onChoose={() => { if (!canDockDirectory) { setDirectory(false); setCompactChat(false); directoryButton.current?.focus(); } }} />
      </aside>
      {sidebar && <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="目录栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} />}
      <section className="writing-paper module" role="region" aria-label="正文阅读" hidden={!paper}>
        {currentId ? moduleBinding ? <WorkspaceModulePaper key={`${currentId}:${moduleBinding.id}`} workspaceId={currentId} binding={moduleBinding} /> : <Paper file={file} loading={loading} /> : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>}
      </section>
      {currentId && <>
        {chat && !compact && <ResizeHandle value={plan.chat} min={CHAT.min} max={Math.min(CHAT.max, width - plan.sidebar - plan.paperMin - (sidebar ? 12 : 6))}
          invert label="对话栏宽度" onChange={prefs.setChatWidth} onReset={() => prefs.setChatWidth(CHAT.default)} />}
        <div id="writing-chat" className="writing-chat module" hidden={!chat} style={{ width: compact ? "100%" : plan.chat }}><ConversationPanel /></div>
      </>}
    </div>
  </div>;
}
