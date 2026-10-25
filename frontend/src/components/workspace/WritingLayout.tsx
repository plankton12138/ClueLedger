import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CHAT, SIDEBAR, useLayoutStore, writingLayout } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { EmptyState } from "../ui/StatusMessage";
import WorkspaceToc from "./WorkspaceToc";
import ConversationPanel from "./ConversationPanel";
import FontSizeControl from "./FontSizeControl";
import ResizeHandle from "./ResizeHandle";
import Paper from "./Paper";

export default function WritingLayout({ onOpen }: { onOpen: () => void }) {
  const prefs = useLayoutStore();
  const { file, loading, currentId, openDrawer } = useWorkspaceStore();
  const running = useConversationsStore(runningConversation);
  const [width, setWidth] = useState(0);
  const [directory, setDirectory] = useState(false);
  const [singlePane, setSinglePane] = useState<"paper" | "chat">("paper");
  const [display, setDisplay] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(Math.max(0, entries[0].contentRect.width - 24)); });
    observer.observe(node); return () => observer.disconnect();
  }, []);
  const plan = writingLayout(width, { ...prefs, chatVisible: currentId ? prefs.chatVisible : false });
  const sidebar = plan.kind === "three" || plan.kind === "reading";
  const chat = Boolean(currentId && (plan.kind === "single" ? singlePane === "chat" : prefs.chatVisible));
  const paper = plan.kind !== "single" || !chat;
  useEffect(() => { setDirectory(false); setSinglePane("paper"); }, [currentId]);
  function toggleChat() {
    if (plan.kind === "single") setSinglePane(value => value === "paper" ? "chat" : "paper");
    else prefs.toggleChat();
  }
  const chatLabel = running ? "对话 · 进行中" : "对话";
  return <div className="writing-layout" ref={container} data-layout={plan.kind}>
    <div className="writing-tools">
      {!sidebar && <AppDialog title="目录" open={directory} onOpenChange={setDirectory} className="ui-directory-dialog" trigger={<Button size="compact">目录</Button>}>
        <WorkspaceToc onChoose={() => { setDirectory(false); setSinglePane("paper"); }} />
      </AppDialog>}
      {plan.kind === "single" && chat && <Button size="compact" onClick={() => setSinglePane("paper")}>正文</Button>}
      <Button size="compact" aria-pressed={chat} onClick={toggleChat} disabled={!currentId} disabledReason="先打开作品目录">{chatLabel}</Button>
      <AppDialog title="阅读显示" open={display} onOpenChange={setDisplay} trigger={<Button size="compact">阅读显示</Button>}><FontSizeControl /></AppDialog>
      <Button size="compact" disabled={!currentId} disabledReason="先打开作品目录" onClick={() => void openDrawer()}>活动记录</Button>
    </div>
    <div className="writing-panes">
      {sidebar && <><div className="writing-toc module" style={{ width: plan.sidebar }}><WorkspaceToc /></div>
        <ResizeHandle value={plan.sidebar} min={SIDEBAR.min} max={Math.min(SIDEBAR.max, width - plan.chat - plan.paperMin - 12)} label="目录栏宽度"
          onChange={prefs.setSidebarWidth} onReset={() => prefs.setSidebarWidth(SIDEBAR.default)} /></>}
      <section className="writing-paper module" role="region" aria-label="正文阅读" hidden={!paper}>
        {currentId ? <Paper file={file} loading={loading} /> : <EmptyState action={<Button variant="primary" onClick={onOpen}>打开 / 新建</Button>}>打开一个作品目录</EmptyState>}
      </section>
      {currentId && <>
        {chat && plan.kind !== "single" && <ResizeHandle value={plan.chat} min={CHAT.min} max={Math.min(CHAT.max, width - plan.sidebar - plan.paperMin - (sidebar ? 12 : 6))}
          invert label="对话栏宽度" onChange={prefs.setChatWidth} onReset={() => prefs.setChatWidth(CHAT.default)} />}
        <div className="writing-chat module" hidden={!chat} style={{ width: plan.kind === "single" ? "100%" : plan.chat }}><ConversationPanel /></div>
      </>}
    </div>
  </div>;
}
