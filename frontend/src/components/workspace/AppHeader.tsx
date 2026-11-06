import { useState } from "react";
import { BookOpenText, Clock3, CreditCard, LayoutGrid, LogOut, Settings, UserRound } from "lucide-react";
import { pageHash, navigate, type AppPage, type WorkspacePage } from "../../state/navigation";
import { usePagePositions } from "../../state/pagePositions";
import { useLayoutStore } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { useBillingStore } from "../../state/billing";
import { useModelsStore } from "../../state/models";
import { useSessionStore } from "../../state/session";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import FontSizeControl from "./FontSizeControl";
import RemoveWorkspaceDialog from "./RemoveWorkspaceDialog";
import RenameWorkspaceDialog from "./RenameWorkspaceDialog";

function WorkspaceNavigation({ page, workspaceId }: { page: AppPage | null; workspaceId: string | null }) {
  const writing: WorkspacePage | null = workspaceId ? { kind: "writing", workspaceId } : null;
  // 写作区只负责阅读结构与正文；创作修改统一从对话进入。
  const writingActive = page?.kind === "writing" || page?.kind === "ideas";
  return <nav className="workspace-navigation" aria-label="作品页面">
    {writing
      ? <a className="ui-nav-link" href={pageHash(writing)} aria-current={writingActive ? "page" : undefined}>
        <BookOpenText size={15} strokeWidth={1.8} aria-hidden="true" />写作</a>
      : <span aria-disabled="true"><BookOpenText size={15} strokeWidth={1.8} aria-hidden="true" />写作</span>}
    <a className="ui-nav-link" href={pageHash({ kind: "library", workspaceContextId: workspaceId ?? undefined })} aria-current={page?.kind === "library" ? "page" : undefined}>
      <LayoutGrid size={15} strokeWidth={1.8} aria-hidden="true" />模块库</a>
  </nav>;
}

function WorkspaceMenu({ onOpen }: { onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const { currentId, bindings, refreshAll, pruneMissing } = useWorkspaceStore();
  const stale = bindings.filter(item => !item.exists).length;
  const current = currentId ? bindings.find(item => item.id === currentId) ?? null : null;
  return <>
    <AppDialog open={open} onOpenChange={setOpen} title="作品" trigger={<Button size="compact" data-dialog-return="workspace">作品</Button>}>
      <div className="ui-action-list">
        <Button onClick={() => { setOpen(false); onOpen(); }}>打开 / 新建</Button>
        <Button disabled={!currentId} disabledReason="先打开作品目录" onClick={() => { setOpen(false); void refreshAll(); }}>重新读取文件</Button>
        {current && <Button onClick={() => { setOpen(false); setRenaming(true); }}>重命名作品…</Button>}
        {current && <Button variant="danger" onClick={() => { setOpen(false); setRemoving(true); }}>删除作品…</Button>}
        {stale > 0 && <Button variant="danger" onClick={() => void pruneMissing()}>清理失效（{stale}）</Button>}
        {!currentId && <p className="ui-field-hint">先打开作品目录。</p>}
      </div>
    </AppDialog>
    <RenameWorkspaceDialog open={renaming} workspace={current} onOpenChange={setRenaming} />
    <RemoveWorkspaceDialog open={removing} workspace={current} onOpenChange={setRemoving} />
  </>;
}

function SubscriptionEntry() {
  const mode = useSessionStore((state) => state.mode);
  const active = useBillingStore((state) => state.entitlement?.active ?? false);
  const openDialog = useBillingStore((state) => state.openDialog);
  if (mode !== "server") return null;
  return <Button size="compact" onClick={openDialog} title="订阅">
    <CreditCard size={16} strokeWidth={2} aria-hidden="true" />{active ? "订阅有效" : "订阅"}
  </Button>;
}

function AccountMenu() {
  const mode = useSessionStore((state) => state.mode);
  const user = useSessionStore((state) => state.user);
  const busy = useSessionStore((state) => state.busy);
  const error = useSessionStore((state) => state.error);
  const logout = useSessionStore((state) => state.logout);
  const [open, setOpen] = useState(false);
  if (mode !== "server" || user === null) return null;
  return <AppDialog title="账号" open={open} onOpenChange={setOpen} trigger={
    <Button size="compact" data-dialog-return="account" title={user.email}>
      <UserRound size={16} strokeWidth={2} aria-hidden="true" />
      <span className="account-email">{user.email}</span>
    </Button>}>
    <div className="ui-action-list">
      <p className="ui-field-hint">当前账号：{user.email}</p>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <Button variant="danger" busy={busy} disabled={busy} onClick={() => void logout()}>
        <LogOut size={16} strokeWidth={2} aria-hidden="true" />退出登录
      </Button>
    </div>
  </AppDialog>;
}

export default function AppHeader({ page, onOpen, onSelect }: { page: AppPage | null; onOpen: () => void; onSelect: (id: string) => void }) {
  const { bindings, currentId, openDrawer } = useWorkspaceStore();
  const running = useConversationsStore(runningConversation);
  const [settings, setSettings] = useState(false);
  const openModels = useModelsStore(state => state.openSettings);
  function returnToRunning() {
    if (!currentId) return;
    const last = usePagePositions.getState().pages[currentId]?.writing;
    navigate(last?.kind === "writing" ? last : { kind: "writing", workspaceId: currentId });
    useLayoutStore.getState().revealChat();
  }
  return <header className="app-header">
    <div className="workspace-picker"><span className="app-name" title="ClueLedger">线簿</span>
      <select className="ui-input" aria-label="选择作品目录" value={currentId ?? ""} onChange={event => event.target.value && onSelect(event.target.value)}>
        <option value="">（未打开目录）</option>{bindings.map(item => <option key={item.id} value={item.id}>{item.name}{item.exists ? "" : "（目录已不在）"}</option>)}
      </select><WorkspaceMenu onOpen={onOpen} />
    </div>
    <WorkspaceNavigation page={page} workspaceId={currentId} />
    <div className="app-tools">
      {running && <Button size="compact" variant="primary" onClick={returnToRunning}>生成中</Button>}
      <Button size="compact" disabled={!currentId} disabledReason="先打开作品目录" onClick={() => void openDrawer()}>
        <Clock3 size={16} strokeWidth={2} aria-hidden="true" />活动记录
      </Button>
      <SubscriptionEntry />
      <AccountMenu />
      <AppDialog title="设置" open={settings} onOpenChange={setSettings} trigger={<Button size="compact" data-dialog-return="settings">
        <Settings size={16} strokeWidth={2} aria-hidden="true" />设置</Button>}>
        <div className="ui-action-list"><Button onClick={() => { setSettings(false); openModels(); }}>模型设置</Button><FontSizeControl /></div>
      </AppDialog>
    </div>
  </header>;
}
