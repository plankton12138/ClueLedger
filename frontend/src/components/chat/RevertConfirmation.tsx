import { useRef, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { errorMessage } from "../../api/errors";

export default function RevertConfirmation({ commit, paths, loadPaths, triggerLabel = "撤销这次修改", onConfirm }: {
  commit: string; paths: string[]; loadPaths?: () => Promise<string[]>; triggerLabel?: string; onConfirm: (commit: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const locked = useRef(false);
  const reverting = useWorkspaceStore(state => state.reverting);
  const [affected, setAffected] = useState(paths);
  const [reading, setReading] = useState(false), [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  function changeOpen(value: boolean) {
    if (pending) return;
    const epoch = ++generation.current;
    setOpen(value); setError(null); setAffected(paths);
    if (!value || !loadPaths) return;
    setReading(true);
    void loadPaths().then(items => { if (epoch === generation.current) setAffected(items); })
      .catch(reason => { if (epoch === generation.current) setError(errorMessage(reason, "撤销确认")); })
      .finally(() => { if (epoch === generation.current) setReading(false); });
  }
  async function confirm() {
    if (locked.current || reading || error) return;
    locked.current = true; setPending(true);
    try { await onConfirm(commit); setOpen(false); }
    finally { locked.current = false; setPending(false); }
  }
  return (
    <AlertDialog.Root open={open} onOpenChange={changeOpen}>
      <AlertDialog.Trigger asChild>
        <Button size="compact" variant="danger" disabled={reverting}>{triggerLabel}</Button>
      </AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="ui-dialog-overlay" style={{ zIndex: 70 }} />
        <AlertDialog.Content style={{ zIndex: 71 }} className="fixed left-1/2 top-1/2 max-h-[80vh] w-[min(32rem,90vw)] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-lg border border-ui-edge bg-ui-surface p-5 shadow-xl">
          <AlertDialog.Title className="font-bold">撤销这次修改</AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-sm">将回退以下提交涉及的文件修改。确认后执行一次撤销。</AlertDialog.Description>
          <code className="mt-3 block break-all text-xs">{commit}</code>
          {reading && <p role="status">正在核对影响文件…</p>}
          {error && <p role="alert">{error}</p>}
          <ul className="my-3 list-inside list-disc break-words text-sm">{affected.map(path => <li key={path}>{path}</li>)}</ul>
          {!reading && !error && !affected.length && <p>此提交没有涉及正文或设定文件。</p>}
          <div className="flex justify-end gap-2">
            <AlertDialog.Cancel asChild><Button disabled={pending}>取消</Button></AlertDialog.Cancel>
            <Button variant="danger" disabled={pending || reverting || reading || Boolean(error)} onClick={() => void confirm()}>{pending ? "撤销中…" : "确认撤销"}</Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
