import { useState } from "react";
import { requestApi } from "../../api/workspace";
import { errorMessage } from "../../api/errors";
import { useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";

export default function StoryBatches({ workspaceId }: { workspaceId: string }) {
  const conversationId = useConversationsStore(state => state.selectedId);
  const operation = useConversationsStore(state => conversationId ? state.entries[conversationId]?.operation : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!operation?.pending_units.length || operation.status !== "completed") return null;
  async function next() {
    if (!operation || busy) return;
    setBusy(true); setError("");
    try {
      const accepted = await requestApi<{ conversation_id: string }>(
        `/api/file-workspaces/${workspaceId}/story-batches/${operation.id}/continue`, { method: "POST" });
      await useConversationsStore.getState().select(accepted.conversation_id);
    } catch (cause) { setError(errorMessage(cause, "继续构思任务")); }
    finally { setBusy(false); }
  }
  return <div className="story-comparison">
    <Button size="compact" disabled={busy} onClick={() => void next()}>继续下一组</Button>
    <span>还有 {operation.pending_units.length} 个节点待处理</span>
    {error && <p role="alert">{error}</p>}
  </div>;
}
