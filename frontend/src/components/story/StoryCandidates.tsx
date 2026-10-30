import { useState } from "react";
import { listStoryCandidates, type StoryCandidate } from "../../api/story";
import { errorMessage } from "../../api/errors";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

const STATUS: Record<string, string> = {
  draft: "草稿", reviewing: "核对中", repairing: "修正中",
  needs_input: "等待作者处理", committed: "已保存到正文",
  paused: "已暂停", needs_attention: "需要处理",
};

export default function StoryCandidates({ workspaceId }: { workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<StoryCandidate[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true);
    setError("");
    try { setItems(await listStoryCandidates(workspaceId)); }
    catch (cause) { setError(errorMessage(cause, "读取候选正文")); }
    finally { setLoading(false); }
  }
  return <>
    <Button size="compact" onClick={() => { setItems([]); setOpen(true); void load(); }}>候选正文</Button>
    <AppDialog open={open} title="候选正文" onOpenChange={setOpen}>
      <div className="max-h-[70vh] space-y-4 overflow-auto p-1">
        <Button size="compact" onClick={() => void load()} disabled={loading}>刷新</Button>
        {loading && <p>正在读取…</p>}
        {error && <StatusMessage tone="error">{error}</StatusMessage>}
        {!loading && !error && items.length === 0 && <p>还没有候选正文。</p>}
        {items.map(item => <article key={item.id} className="space-y-2 border-b border-panel-edge pb-4">
          <h3 className="font-bold">{STATUS[item.status] ?? "草稿"}</h3>
          <p className="whitespace-pre-wrap break-words">{item.author_text}</p>
          {Object.entries(item.documents).map(([path, text]) => <details key={path}>
            <summary className="cursor-pointer break-words">{path}</summary>
            <pre className="whitespace-pre-wrap break-words font-serif leading-7">{text}</pre>
          </details>)}
          {item.report && <details><summary className="cursor-pointer">核对结果</summary>
            <ul>{item.report.checks.map(check => <li key={check.id} className="my-2 whitespace-pre-wrap break-words">
              {check.status === "pass" ? "通过" : check.status === "fail" ? "需调整" : "未确认"}：{check.reason}
              {check.quote && <blockquote className="border-l-2 pl-2">{check.quote}</blockquote>}
            </li>)}</ul>
          </details>}
        </article>)}
      </div>
    </AppDialog>
  </>;
}
