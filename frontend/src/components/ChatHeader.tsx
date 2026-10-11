/**
 * 对话面板头部（P9 T6，D4）：「新会话」入口 + 模态防误触确认（ui/AlertDialog）。
 * 确认风格对齐文章删除：说明后果（旧对话归档保留、正文/账本不动）+
 * 显式确认键；写作中按钮禁用（服务端另有 409 硬守卫）。
 */

import { useState } from "react";
import { resetSession } from "../api/rest";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

export default function ChatHeader({
  slug,
  running,
  onResetDone,
}: {
  slug: string;
  running: boolean;
  /** reset 成功后回调（App 触发 WS 重连，快照重放得到空会话）。 */
  onResetDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleReset = async () => {
    setBusy(true);
    setError(null);
    try {
      await resetSession(slug);
      setOpen(false);
      onResetDone();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-none items-center justify-between gap-2 border-b border-line bg-ink-soft/60 px-3 py-1.5">
      <span className="truncate text-xs tracking-widest text-gold-soft">主编对话</span>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (busy) return;
          setOpen(next);
          if (!next) setError(null);
        }}
      >
        <AlertDialogTrigger asChild>
          <Button
            variant="outline"
            disabled={running}
            title={running ? "写作中不可开新会话" : "归档当前对话并开新会话"}
            className="h-auto flex-none px-2 py-0.5 text-[11px] text-paper/70 hover:border-gold/60 hover:bg-transparent hover:text-gold"
          >
            新会话
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogTitle>开新会话？</AlertDialogTitle>
          <AlertDialogDescription>
            开新会话后，旧对话将归档保留在磁盘（可找回），
            <span className="text-gold-soft">正文与故事账本不受影响</span>。
            {running && <span className="text-overdue">写作中不可开新会话。</span>}
          </AlertDialogDescription>
          {error && <p className="mt-1.5 break-all text-xs text-overdue">{error}</p>}
          <AlertDialogFooter>
            <AlertDialogAction
              disabled={busy || running}
              onClick={() => void handleReset()}
              className="border-gold/60 text-gold hover:bg-gold/10 hover:text-gold"
            >
              {busy ? "归档中…" : "确认开新会话"}
            </AlertDialogAction>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
