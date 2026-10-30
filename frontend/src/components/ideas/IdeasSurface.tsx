/** 唯一的灵感入口：可拖动的小按钮与短内容面板，页面切换不重建业务草稿。 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { GripVertical, Lightbulb, Minus, MoreHorizontal, Search } from "lucide-react";
import { navigate } from "../../state/navigation";
import { useConversationsStore } from "../../state/conversations";
import { referencePreview, useIdeasStore, useIdeaScope, type SavedIdea } from "../../state/ideas";
import { useIdeasSurfaceStore } from "../../state/ideasSurface";
import { useLayoutStore } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import IdeaCapture, { type IdeaCaptureHandle } from "./IdeaCapture";
import IdeaList from "./IdeaList";
import { useIdeasPosition } from "./useIdeasPosition";

export default function IdeasSurface() {
  const { open, expanded, hide, show, setExpanded, entryId } = useIdeasSurfaceStore();
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const scope = useIdeaScope();
  const error = useIdeasStore(state => state.error);
  const notice = useIdeasStore(state => state.notice);
  const clearNotice = useIdeasStore(state => state.clearNotice);
  const storeWorkspace = useIdeasStore(state => state.workspaceId);
  const fontScale = useLayoutStore(state => state.fontScale);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const pendingAttach = useRef(false);
  const frame = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const capture = useRef<IdeaCaptureHandle>(null);
  const floating = useIdeasPosition(frame, open, workspaceId);

  const close = useCallback(() => {
    setActionError(null);
    const origin = useIdeasSurfaceStore.getState().origin;
    hide();
    requestAnimationFrame(() => {
      if (origin?.isConnected && origin !== document.body) origin.focus();
      else launcher.current?.focus();
    });
  }, [hide]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || !event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.key.toLowerCase() !== "i") return;
      if (!useWorkspaceStore.getState().currentId || document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      event.preventDefault();
      if (useIdeasSurfaceStore.getState().open) close(); else show();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, show]);

  useEffect(() => {
    if (!open || !workspaceId) return;
    let active = true;
    setActionError(null);
    void (async () => {
      const store = useIdeasStore.getState();
      await store.load(workspaceId);
      if (!active || useWorkspaceStore.getState().currentId !== workspaceId) return;
      if (!useIdeasSurfaceStore.getState().expanded) {
        await store.setShowDeleted(false);
        const current = useIdeasStore.getState().scopes[workspaceId];
        if (current?.query) await store.setQuery("");
      }
    })();
    return () => { active = false; };
  }, [open, workspaceId, setExpanded]);

  useEffect(() => {
    if (!open || !entryId || storeWorkspace !== workspaceId) return;
    let active = true;
    setExpanded(true);
    void useIdeasStore.getState().select(entryId).then(() => {
      if (active && useIdeasSurfaceStore.getState().entryId === entryId) useIdeasSurfaceStore.getState().consumeEntry();
    });
    return () => { active = false; };
  }, [open, entryId, storeWorkspace, workspaceId, setExpanded]);

  useLayoutEffect(() => { if (open && storeWorkspace === workspaceId) capture.current?.focus(); }, [open, workspaceId, storeWorkspace]);
  useEffect(() => {
    if (!open || error || actionError || scope.lastTrashed || !notice || !["已记录。", "已加入对话。", "已保存为新的灵感修订。", "内容没有变化。"].includes(notice)) return;
    const timer = window.setTimeout(() => { if (useIdeasStore.getState().notice === notice) clearNotice(); }, 3000);
    return () => window.clearTimeout(timer);
  }, [open, notice, error, actionError, scope.lastTrashed, clearNotice]);

  function targetChat() {
    const chat = useConversationsStore.getState();
    return { workspaceId, conversationId: chat.selectedId };
  }

  function attach(saved: SavedIdea, target: ReturnType<typeof targetChat>) {
    const chat = useConversationsStore.getState();
    if (useWorkspaceStore.getState().currentId !== saved.workspaceId || target.workspaceId !== saved.workspaceId || chat.workspaceId !== saved.workspaceId || chat.selectedId !== target.conversationId) {
      if (useWorkspaceStore.getState().currentId === saved.workspaceId) setActionError("[IDEA_CHAT_CHANGED] 加入对话：当前对话已切换。建议：在目标对话中重新加入这条灵感。");
      return;
    }
    chat.addIdeaReference({ ideaId: saved.ideaId, revisionId: saved.revisionId, preview: referencePreview(saved.body), chars: Array.from(saved.body).length });
    useIdeasStore.setState({ notice: "已加入对话。" });
    useLayoutStore.getState().revealChat();
    navigate({ kind: "writing", workspaceId: saved.workspaceId });
  }

  async function takeToChat(ideaId?: string) {
    if (pendingAttach.current) return;
    pendingAttach.current = true; setActionBusy(true); setActionError(null);
    const target = targetChat();
    try {
      if (ideaId) {
        const saved = await useIdeasStore.getState().prepareReference(ideaId);
        if (saved) attach(saved, target);
      } else {
        const result = await useIdeasStore.getState().capture();
        if (result.status !== "failed") attach(result.value, target);
      }
    } finally { pendingAttach.current = false; setActionBusy(false); }
  }

  const openConversation = async (conversationId: string) => {
    const id = workspaceId;
    await useConversationsStore.getState().select(conversationId);
    if (!id || useWorkspaceStore.getState().currentId !== id) return;
    if (useConversationsStore.getState().selectedId !== conversationId) {
      setActionError("[IDEA_DISCUSSION_UNAVAILABLE] 打开讨论：会话无法读取。建议：核对会话是否已删除。"); return;
    }
    close(); useLayoutStore.getState().revealChat(); navigate({ kind: "writing", workspaceId: id });
  };

  const openSource = async (path: string) => {
    const id = workspaceId;
    await useWorkspaceStore.getState().openPath(path);
    if (!id || useWorkspaceStore.getState().currentId !== id) return;
    if (useWorkspaceStore.getState().selectedPath !== path) {
      setActionError(`[IDEA_SOURCE_UNAVAILABLE] ${path}：来源文件无法打开。建议：核对文件是否已移动或删除。`); return;
    }
    close(); navigate({ kind: "writing", workspaceId: id });
  };

  if (!workspaceId) return null;
  return <div ref={frame} className={`idea-floating${open ? " is-open" : ""}`} style={{ ...floating.style, width: open ? 344 * Math.max(1, fontScale / 100) : undefined }}>
    {!open ? <button ref={launcher} type="button" className="idea-launcher" aria-label="灵感" aria-expanded={false}
      title="拖动可移动；方向键调整位置，Home 复位；Alt+I 打开" {...floating.handlers}
      onClick={() => { if (!floating.consumeDragClick()) show(); }}>
      <Lightbulb size={18} aria-hidden="true" /><span>灵感</span>
    </button> : <section className="idea-surface" role="complementary" aria-label="灵感" data-presentation="floating" onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.key !== "Escape" || (event.target as HTMLElement).closest('[role="dialog"]')) return;
      const menu = (event.target as HTMLElement).closest<HTMLDetailsElement>("details[open]");
      if (menu) { event.stopPropagation(); menu.open = false; menu.querySelector<HTMLElement>("summary")?.focus(); return; }
      event.stopPropagation(); close();
    }}>
      <header className="idea-surface-tools">
        <button type="button" className="idea-drag-handle" aria-label="移动灵感面板" title="拖动可移动；方向键调整位置，Home 复位" {...floating.handlers} onClick={() => floating.consumeDragClick()}>
          <GripVertical size={15} aria-hidden="true" /><span>灵感</span>
        </button>
        <Button size="compact" variant="quiet" className="idea-icon-button" aria-label={expanded ? "回到最近灵感" : "搜索灵感"}
          onClick={() => {
            setExpanded(!expanded); void useIdeasStore.getState().setShowDeleted(false);
            if (scope.query) void useIdeasStore.getState().setQuery("");
            if (!expanded) requestAnimationFrame(() => frame.current?.querySelector<HTMLInputElement>('input[aria-label="搜索灵感"]')?.focus());
          }}><Search size={16} aria-hidden="true" /></Button>
        <details className="idea-options"><summary aria-label="灵感选项"><MoreHorizontal size={16} aria-hidden="true" /></summary>
          <div className="idea-options-menu" onClick={event => { const menu = event.currentTarget.closest("details"); if (menu) menu.open = false; }}>
            <Button size="compact" variant="quiet" onClick={() => { setExpanded(true); void useIdeasStore.getState().setShowDeleted(false); }}>全部灵感</Button>
            <Button size="compact" variant="quiet" onClick={() => { setExpanded(true); void useIdeasStore.getState().setShowDeleted(true); }}>最近删除</Button>
            <Button size="compact" variant="quiet" onClick={() => useIdeasSurfaceStore.getState().resetPosition()}>重置位置</Button>
          </div>
        </details>
        <Button size="compact" variant="quiet" className="idea-icon-button" aria-label="收起灵感" onClick={close}><Minus size={16} aria-hidden="true" /></Button>
      </header>
      <div className="idea-surface-body">
        {storeWorkspace === workspaceId && <IdeaCapture ref={capture} onTakeToChat={() => takeToChat()} actionBusy={actionBusy} />}
        {(error || notice || actionError) && <div className="idea-surface-status"><StatusMessage tone={error || actionError ? "error" : "info"}
          action={<>{scope.lastTrashed && <Button variant="quiet" size="compact" onClick={() => void useIdeasStore.getState().undoTrash()}>撤销删除</Button>}
            <Button variant="quiet" size="compact" aria-label="清除灵感提示" onClick={() => { setActionError(null); clearNotice(); }}>×</Button></>}>
          {error ?? actionError ?? notice}
        </StatusMessage></div>}
        {storeWorkspace === workspaceId ? <IdeaList expanded={expanded} onExpandList={() => setExpanded(true)} actionBusy={actionBusy}
          onTakeToChat={takeToChat} onOpenConversation={openConversation} onOpenSource={openSource} />
          : <StatusMessage>正在读取灵感…</StatusMessage>}
      </div>
    </section>}
  </div>;
}
