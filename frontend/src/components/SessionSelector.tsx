/**
 * 会话选择器（P10 T14，D13）：对话头入口——列出/新建/切换/重命名/删除会话。
 *
 * 平铺会话（不做分支/树）：切换 → 服务端 rebuild + 前端重连 WS 快照重放
 * （复用 useAgentSocket.reconnect，正文面板不清）。删除走 AlertDialog 确认；
 * 写作中按钮禁用（服务端另有 409 硬守卫）。
 */

import { useEffect, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  activateSession,
  createSession,
  deleteSession,
  listSessions,
  renameSession,
  type SessionSummary,
  type SessionsResponse,
} from "../api/rest";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/** 会话行的次要信息（纯函数，便于用例覆盖）：消息条数 + 最后活跃时间。 */
export function sessionMetaLabel(session: SessionSummary): string {
  const when = session.last_active.replace("T", " ");
  return `${session.message_count} 条 · ${when}`;
}

export default function SessionSelector({
  slug,
  running,
  onSessionChanged,
}: {
  slug: string;
  running: boolean;
  /** 切换/新建/删除活跃会话后回调（App 触发 WS 重连，快照重放）。 */
  onSessionChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<SessionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renameTarget, setRenameTarget] = useState<SessionSummary | null>(null);
  const [renameText, setRenameText] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);

  const refresh = async () => {
    try {
      setData(await listSessions(slug));
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  // 展开时拉取列表；换工作区（slug 变化）时清空旧数据。
  useEffect(() => {
    if (open) {
      void refresh();
    } else {
      setData(null);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, slug]);

  const active = data?.sessions.find((s) => s.active) ?? null;

  const run = async (action: () => Promise<unknown>, reconnect: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
      if (reconnect) onSessionChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const handleActivate = (session: SessionSummary) => {
    if (session.active) return;
    void run(async () => activateSession(slug, session.id), true);
  };

  const handleCreate = () => {
    void run(async () => createSession(slug), true);
  };

  const handleRename = () => {
    const title = renameText.trim();
    if (!renameTarget || !title) return;
    void run(async () => renameSession(slug, renameTarget.id, title), false).then(() =>
      setRenameTarget(null),
    );
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    const wasActive = deleteTarget.active;
    void run(async () => deleteSession(slug, deleteTarget.id), wasActive).then(() =>
      setDeleteTarget(null),
    );
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            title="会话：切换 / 新建 / 重命名 / 删除"
            className="h-auto min-w-0 max-w-[40%] flex-none gap-1 px-2 py-0.5 text-[11px] text-paper/70 hover:border-gold/60 hover:bg-transparent hover:text-gold"
          >
            <span className="truncate">{active ? active.title : "会话"}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-72 p-2 text-xs">
          <div className="mb-1.5 flex items-center justify-between px-1">
            <span className="tracking-widest text-gold-soft">会话</span>
            <Button
              variant="ghost"
              disabled={running || busy}
              onClick={handleCreate}
              title={running ? "写作中不可新建会话" : "新建会话"}
              className="h-auto gap-1 px-1.5 py-0.5 text-[11px] text-paper/70 hover:bg-transparent hover:text-gold"
            >
              <Plus size={12} /> 新建
            </Button>
          </div>
          {error && <p className="mb-1.5 break-all px-1 text-overdue">{error}</p>}
          <ul className="max-h-64 overflow-auto">
            {(data?.sessions ?? []).map((session) => (
              <li
                key={session.id}
                className="flex items-center gap-1 rounded px-1 py-1 hover:bg-ink-soft/60"
              >
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => handleActivate(session)}
                  className="min-w-0 flex-1 text-left"
                >
                  <span className="flex items-center gap-1">
                    <span className={session.active ? "text-gold" : "text-paper/40"}>
                      {session.active ? "●" : "○"}
                    </span>
                    <span className="truncate text-paper/90">{session.title}</span>
                  </span>
                  <span className="block truncate pl-3 text-[10px] text-paper/40">
                    {sessionMetaLabel(session)}
                  </span>
                </button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setRenameTarget(session);
                    setRenameText(session.title);
                  }}
                  title="重命名"
                  aria-label="重命名会话"
                  className="h-auto px-1 py-0.5 text-paper/50 hover:bg-transparent hover:text-gold"
                >
                  <Pencil size={12} />
                </Button>
                <Button
                  variant="ghost"
                  disabled={running || busy}
                  onClick={() => setDeleteTarget(session)}
                  title={running ? "写作中不可删除会话" : "删除会话"}
                  aria-label="删除会话"
                  className="h-auto px-1 py-0.5 text-paper/50 hover:bg-transparent hover:text-overdue"
                >
                  <Trash2 size={12} />
                </Button>
              </li>
            ))}
          </ul>
        </PopoverContent>
      </Popover>

      {/* 重命名（AlertDialog 容纳内联输入，复用确认框焦点管理） */}
      <AlertDialog
        open={renameTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRenameTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogTitle>重命名会话</AlertDialogTitle>
          <AlertDialogDescription>只改显示名，不影响会话内容与活跃状态。</AlertDialogDescription>
          <Input
            value={renameText}
            onChange={(e) => setRenameText(e.target.value)}
            className="mt-2"
            autoFocus
          />
          <AlertDialogFooter>
            <AlertDialogAction disabled={busy || !renameText.trim()} onClick={handleRename}>
              保存
            </AlertDialogAction>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除确认 */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogTitle>删除会话？</AlertDialogTitle>
          <AlertDialogDescription>
            将删除「{deleteTarget?.title}」的对话记录（不可恢复）。
            <span className="text-gold-soft">正文与故事账本不受影响</span>。
            {deleteTarget?.active && <span className="text-overdue">该会话当前活跃，删除后将切换到其他会话。</span>}
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogAction
              disabled={busy}
              onClick={handleDelete}
              className="border-overdue/60 text-overdue hover:bg-overdue/10 hover:text-overdue"
            >
              删除
            </AlertDialogAction>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}