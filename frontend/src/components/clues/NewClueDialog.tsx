/** 从零建立一条线索设计身份；不写正文贡献，也不假装已经埋设。 */
import { useEffect, useState } from "react";
import { clueRequestKey, createClue, fetchClues } from "../../api/clues";
import { errorMessage } from "../../api/errors";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

interface Props {
  workspaceId: string;
  asOf: number | null;
  onCreated: (clueId: string) => void;
  onClose: () => void;
}

export default function NewClueDialog({ workspaceId, asOf, onCreated, onClose }: Props) {
  const [title, setTitle] = useState("");
  const [revision, setRevision] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void fetchClues(workspaceId, null, asOf)
      .then(page => {
        if (alive) setRevision(page.ledger_revision);
      })
      .catch(cause => {
        if (alive) setError(errorMessage(cause, "读取线索账本"));
      });
    return () => {
      alive = false;
    };
  }, [workspaceId, asOf]);

  async function submit() {
    if (busy || !title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const detail = await createClue(workspaceId, {
        title: title.trim(),
        requestKey: clueRequestKey("clue-create"),
        expectedRevision: revision || "empty",
        asOf,
      });
      onCreated(detail.clue_id);
      onClose();
    } catch (cause) {
      setError(errorMessage(cause, "新建线索设计"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppDialog open onOpenChange={open => { if (!open) onClose(); }} title="新建线索设计">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-sub">先给这条线索一个作者能辨认的标题；它不会产生已埋设、已披露或已回收记录。</p>
        <label className="block">线索标题
          <input className="ui-input mt-1 w-full" value={title} maxLength={200} autoFocus
            onChange={event => setTitle(event.target.value)} placeholder="例如：遗书背面的字" />
        </label>
        {error && <StatusMessage tone="error">{error}</StatusMessage>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button size="compact" variant="quiet" onClick={onClose}>取消</Button>
          <Button size="compact" variant="primary" busy={busy} disabled={!title.trim() || !revision}
            disabledReason={!title.trim() ? "先写一个标题" : "正在读取账本版本"} onClick={() => void submit()}>
            建立线索
          </Button>
        </div>
      </div>
    </AppDialog>
  );
}
