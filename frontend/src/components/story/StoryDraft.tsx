import { useEffect, useState } from "react";
import { request } from "../../api/workspace";
import { errorMessage } from "../../api/errors";
import { useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";

type Draft = { revision: string; operations: number; constraints: {
  id: string; text: string; scope: { kind: string; target_id: string };
  source_ref: { module_id: string; revision_id: string; entry_id: string } | null;
}[] };

/** 确认只针对眼前草稿的来源原文；不会把模块全文里的指令当作授权。 */
export default function StoryDraft({ workspaceId }: { workspaceId: string }) {
  const selected = useConversationsStore(s => s.selectedId);
  const operation = useConversationsStore(s => selected ? s.entries[selected]?.operation : null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setDraft(null); setError("");
    if (!operation || !["needs_attention", "paused", "awaiting_answer", "failed"].includes(operation.status)) return;
    void request<{ draft: Draft | null }>(`/${workspaceId}/story/drafts/${operation.id}`).then(result => {
      if (!cancelled) setDraft(result.draft);
    }).catch(cause => { if (!cancelled) setError(errorMessage(cause, "读取构思草稿")); });
    return () => { cancelled = true; };
  }, [workspaceId, operation?.id, operation?.status, operation?.updated_at]);
  async function confirm() {
    if (!draft || !operation || busy) return;
    setBusy(true); setError("");
    try {
      await request(`/${workspaceId}/story/drafts/${operation.id}/confirm-constraints`, {
        method: "POST", body: JSON.stringify({ draft_revision: draft.revision }),
      });
      await useConversationsStore.getState().resume();
    } catch (cause) { setError(errorMessage(cause, "确认模块约定")); }
    finally { setBusy(false); }
  }
  if (!draft && !error) return null;
  return <div className="story-draft">
    {draft && <details open={draft.constraints.length > 0}><summary>任务草稿 · 尚未整体提交</summary>
      {draft.constraints.map(c => <article key={c.id}><p>{c.text}</p>
        <small>{c.scope.kind === "book" ? "全书" : c.scope.target_id} · 必需</small>
        {c.source_ref && <details><summary>模块来源</summary><p>{c.source_ref.module_id} / {c.source_ref.revision_id} / {c.source_ref.entry_id}</p></details>}
      </article>)}
      {draft.constraints.length > 0 && operation?.capabilities.continue
        ? <><p>请逐项核对以上内容及必需强度；确认后用于当前草稿。</p>
          <Button disabled={busy} onClick={() => void confirm()}>确认这些必需约定并继续</Button></>
        : <p>待补安排保留在草稿中；需要确认的约定可在对话中集中答复。</p>}
      {draft.constraints.length === 0 && operation?.capabilities.continue && <Button disabled={busy} onClick={() => void useConversationsStore.getState().resume()}>继续构思任务</Button>}
    </details>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
