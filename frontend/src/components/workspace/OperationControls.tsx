import { useEffect, useState } from "react";
import { requestApi } from "../../api/workspace";
import { readOperation, readOperationResult, type OperationResult, pauseOperation, continueOperation, interjectOperation, operationKey, type Operation } from "../../api/operations";
import { errorMessage } from "../../api/errors";
import { Button } from "../ui/Button";

export default function OperationControls({ operationId, probe = false, onDone }: { operationId: string; probe?: boolean; onDone?: (result: OperationResult) => void }) {
  const [operation, setOperation] = useState<Operation | null>(null);
  const [text, setText] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [current, events] = await Promise.all([readOperation(operationId), requestApi<{ events: { type: string; text?: string; generation: number }[] }>(`/api/operations/${operationId}/events`)]);
        if (cancelled) return;
        setOperation(current);
        setOutput(events.events.filter(event => event.type === "text_delta" && event.generation === current.generation).map(event => event.text ?? "").join(""));
        if (current.status === "completed" || current.status === "failed") {
          const result = await readOperationResult(operationId);
          if (!cancelled) onDone?.(result);
        } else timer = setTimeout(() => void refresh(), 600);
      } catch (cause) { if (!cancelled) setError(errorMessage(cause, "读取任务")); }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [operationId]);
  async function control(action: "pause" | "continue" | "interject") {
    if (!operation || busy) return;
    setBusy(true); setError("");
    try {
      const key = operationKey(action);
      setOperation(await (action === "pause" ? pauseOperation(operationId, key)
        : action === "continue" ? continueOperation(operationId, key, operation.generation)
        : interjectOperation(operationId, key, operation.generation, text)));
      setText("");
    } catch (cause) { setError(errorMessage(cause, "控制任务")); }
    finally { setBusy(false); }
  }
  return <div className="min-w-0 space-y-2" aria-label="任务控制">
    <div className="flex flex-wrap gap-2">
      {operation?.capabilities.pause && <Button size="compact" disabled={busy} onClick={() => void control("pause")}>暂停</Button>}
      {operation?.capabilities.continue && <Button size="compact" disabled={busy} onClick={() => void control("continue")}>继续</Button>}
      {operation?.capabilities.interject && <>
        {probe ? <select aria-label="测试目标" value={text} onChange={event => setText(event.target.value)}>
          <option value="">选择测试目标</option><option>响应测试</option><option>工具测试</option>
        </select> : <input className="ui-input min-w-0" aria-label="补充任务要求" value={text} onChange={event => setText(event.target.value)} />}
        <Button size="compact" disabled={busy || !text.trim()} onClick={() => void control("interject")}>插话</Button>
      </>}
    </div>
    {operation?.note && <p role="status">{operation.note}</p>}
    {output && <details><summary>本次输出</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{output}</pre></details>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
