import { useEffect, useState } from "react";
import { requestApi, conversationMessages } from "../../api/workspace";
import { readOperation, pauseOperation, continueOperation, operationKey, type Operation } from "../../api/operations";
import { errorMessage } from "../../api/errors";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";

export default function ReaderPreview({ workspaceId, variantId, revision, cutoff }: {
  workspaceId: string; variantId: string; revision: string; cutoff: string | null;
}) {
  const key = `reader-preview:${workspaceId}`;
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState<{ conversation_id: string; operation_id: string } | null>(() => {
    try { return JSON.parse(localStorage.getItem(key) ?? "null"); } catch { return null; }
  });
  const [operation, setOperation] = useState<Operation | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open || !session) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [messages, current] = await Promise.all([
          conversationMessages(workspaceId, session!.conversation_id), readOperation(session!.operation_id),
        ]);
        if (cancelled) return;
        setOperation(current);
        const completed = new Map<string, string>();
        const streaming = new Map<string, string>();
        for (const event of messages.events) {
          if (event.type === "assistant_message") completed.set(event.message_id ?? event.turn_id, event.text);
          if (event.type === "assistant_block_delta" && event.kind === "text") {
            streaming.set(event.message_id, (streaming.get(event.message_id) ?? "") + event.text);
          }
        }
        for (const [id, value] of streaming) if (!completed.has(id)) completed.set(id, value);
        setText([...completed.values()].join("\n\n"));
        if (current.error) setError(`[${current.error.code}] 试读：${current.error.why}。建议：${current.error.fix}`);
        if (current.status === "running" || current.status === "accepted" || current.status === "paused") {
          timer = setTimeout(() => void refresh(), 800);
        }
      } catch (cause) { if (!cancelled) setError(errorMessage(cause, "读取试读")); }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open, session, workspaceId]);
  async function start() {
    if (!cutoff) return;
    setBusy(true); setError("");
    try {
      const accepted = await requestApi<{ conversation_id: string; operation_id: string }>(
        `/api/file-workspaces/${workspaceId}/reader-previews`, { method: "POST", body: JSON.stringify({
          request_key: operationKey("reader"), variant_id: variantId, revision, cutoff,
        }) });
      localStorage.setItem(key, JSON.stringify(accepted));
      setSession(accepted); setText("");
    } catch (cause) { setError(errorMessage(cause, "开始试读")); }
    finally { setBusy(false); }
  }
  async function control(resume: boolean) {
    if (!operation) return;
    setBusy(true);
    try { setOperation(await (resume
      ? continueOperation(operation.id, operationKey("reader-continue"), operation.generation)
      : pauseOperation(operation.id, operationKey("reader-pause")))); }
    catch (cause) { setError(errorMessage(cause, "控制试读")); }
    finally { setBusy(false); }
  }
  return <>
    <Button size="compact" onClick={() => setOpen(true)}>试读</Button>
    <AppDialog open={open} title="试读" onOpenChange={setOpen}>
      <div className="max-h-[70vh] space-y-4 overflow-auto p-1">
        <p>选中截止节点后，试读只使用此前的呈现内容。</p>
        <Button disabled={!cutoff || busy || operation?.capabilities.pause} onClick={() => void start()}>试读到所选节点</Button>
        {operation?.capabilities.pause && <Button disabled={busy} onClick={() => void control(false)}>暂停</Button>}
        {operation?.capabilities.continue && <Button disabled={busy} onClick={() => void control(true)}>继续</Button>}
        {operation && <p>{operation.note || (operation.status === "running" ? "正在试读…" : operation.status === "paused" ? "已暂停" : "试读已结束")}</p>}
        {error && <p role="alert">{error}</p>}
        <div className="whitespace-pre-wrap break-words leading-7" aria-live="polite">{text}</div>
      </div>
    </AppDialog>
  </>;
}
