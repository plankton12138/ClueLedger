import { useEffect, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";

import { navigate } from "../../state/navigation";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";

/**
 * 明确展示完整目录和删除范围；仅在用户点击危险操作按钮后提交实际删除。
 */

interface Props {
  open: boolean;
  workspace: { id: string; name: string; path: string } | null;
  onOpenChange: (open: boolean) => void;
}

export default function RemoveWorkspaceDialog({ open, workspace, onOpenChange }: Props) {
  const remove = useWorkspaceStore((state) => state.remove);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setError(null);
  }, [open, workspace?.id]);

  if (!workspace) return null;

  async function confirm(): Promise<void> {
    if (!workspace || pending) return;
    setPending(true);
    setError(null);
    try {
      const wasCurrent = useWorkspaceStore.getState().currentId === workspace.id;
      if (!(await remove(workspace.id, workspace.path))) {
        setError(useWorkspaceStore.getState().error);
        return;
      }
      if (wasCurrent) {
        const next = useWorkspaceStore.getState().currentId;
        if (next) navigate({ kind: "writing", workspaceId: next });
        else window.location.hash = "";
      }
      onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!pending) onOpenChange(value);
      }}
    >
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="ui-dialog-overlay" style={{ zIndex: 70 }} />
        <AlertDialog.Content
          style={{ zIndex: 71 }}
          className="fixed left-1/2 top-1/2 max-h-[80vh] w-[min(32rem,90vw)] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-lg border border-ui-edge bg-ui-surface p-5 shadow-xl"
        >
          <AlertDialog.Title className="font-bold">删除作品</AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-sm">
            将永久删除这个作品文件夹及其中的全部文件，包括正文、灵感、模块装配记录和版本历史，并清理本作品的会话与应用记录。共享模块库保留。删除后无法通过应用恢复。
          </AlertDialog.Description>
          <div className="mt-3 rounded border border-ui-edge bg-ui-surface px-3 py-2 text-sm">
            <div className="font-bold">{workspace.name}</div>
            <div className="mt-1 break-all text-xs text-ui-muted">{workspace.path}</div>
          </div>
          {error && <p role="alert" className="mt-3 text-sm text-[#a33]">{error}</p>}
          <div className="mt-4 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Button disabled={pending}>取消</Button>
            </AlertDialog.Cancel>
            <Button variant="danger" disabled={pending} onClick={() => void confirm()}>
              {pending ? "删除中…" : "删除作品及文件"}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
