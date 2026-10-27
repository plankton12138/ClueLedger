import { useEffect, useState } from "react";

import { navigate } from "../../state/navigation";
import { useWorkspaceStore } from "../../state/workspace";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";

/**
 * 重命名作品：目录名就是作品名，改名会同时改磁盘文件夹与登记标识；
 * 正文、Git 历史、会话与活动记录随目录和状态一起迁移。
 */

interface Props {
  open: boolean;
  workspace: { id: string; name: string; path: string } | null;
  onOpenChange: (open: boolean) => void;
}

export default function RenameWorkspaceDialog({ open, workspace, onOpenChange }: Props) {
  const rename = useWorkspaceStore((state) => state.rename);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(workspace?.name ?? "");
    setError(null);
  }, [open, workspace?.id, workspace?.name]);

  if (!workspace) return null;

  const clean = name.trim();
  const canSubmit = Boolean(clean) && clean !== workspace.name && !pending;

  async function submit(): Promise<void> {
    if (!workspace || !canSubmit) return;
    if (clean.includes("/") || clean.includes("\\")) {
      setError("作品名不能包含路径分隔符。");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const wasCurrent = useWorkspaceStore.getState().currentId === workspace.id;
      const newId = await rename(workspace.id, clean);
      if (!newId) {
        setError(useWorkspaceStore.getState().error);
        return;
      }
      if (wasCurrent) navigate({ kind: "writing", workspaceId: newId });
      onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={(value) => {
        if (!pending) onOpenChange(value);
      }}
      title="重命名作品"
    >
      <p className="ui-field-hint">
        会同时重命名磁盘上的作品文件夹；正文、Git 历史、会话与活动记录一起迁移。
      </p>
      <p className="mt-2 break-all text-xs text-ui-muted">当前：{workspace.path}</p>
      <div className="mt-3">
        <Field label="新作品名" error={error ?? undefined}>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) void submit();
            }}
          />
        </Field>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button disabled={pending} onClick={() => onOpenChange(false)}>
          取消
        </Button>
        <Button variant="primary" disabled={!canSubmit} onClick={() => void submit()}>
          {pending ? "重命名中…" : "重命名"}
        </Button>
      </div>
    </AppDialog>
  );
}
