/**
 * 本轮灵感引用（0.8 I3）。
 *
 * 引用显示的是保存那一版的原文短预览，不是内部标识，也不把修订号当日常标签。
 * 点开才读取完整原文；只有作者明确点「更新到最新版本」才换修订，界面不自动替换。
 */

import { useState } from "react";

import { errorMessage } from "../../api/errors";
import { getIdea, getIdeaRevision, type IdeaView } from "../../api/ideas";
import { referencePreview } from "../../state/ideas";
import { useConversationsStore, type DraftIdea } from "../../state/conversations";
import { useWorkspaceStore } from "../../state/workspace";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

export default function IdeaReferenceChip({ reference }: { reference: DraftIdea }) {
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const remove = useConversationsStore(state => state.removeIdeaReference);
  const update = useConversationsStore(state => state.updateIdeaReference);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<IdeaView | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function inspect() {
    setOpen(true);
    setError(null);
    setBody(null);
    if (!workspaceId) return;
    setLoading(true);
    try {
      const [current, revision] = await Promise.all([
        getIdea(workspaceId, reference.idea_id),
        getIdeaRevision(workspaceId, reference.idea_id, reference.revision_id),
      ]);
      setView(current);
      setBody(revision.body);
    } catch (caught) {
      setError(errorMessage(caught, "读取引用的灵感"));
    } finally {
      setLoading(false);
    }
  }

  async function updateToLatest() {
    if (!workspaceId) return;
    try {
      const latest = await getIdea(workspaceId, reference.idea_id);
      update({
        ideaId: latest.idea_id,
        revisionId: latest.revision_id,
        preview: referencePreview(latest.body),
        chars: Array.from(latest.body).length,
      });
      setOpen(false);
    } catch (caught) {
      setError(errorMessage(caught, "更新引用"));
    }
  }

  const label = reference.preview || "（没有正文）";

  return (
    <>
      <span className="flex items-center gap-1 rounded border border-amber-edge bg-amber-bg px-1.5 py-0.5 text-amber">
        <button
          type="button"
          className="max-w-40 truncate text-left"
          aria-label={`查看引用的灵感：${label}`}
          title={label}
          onClick={() => void inspect()}
        >
          {label}
        </button>
        <button
          aria-label={`移除灵感 ${label}`}
          className="text-mute"
          onClick={() => remove(reference.idea_id)}
        >
          ×
        </button>
      </span>
      <AppDialog title="引用的灵感" open={open} onOpenChange={setOpen}>
        <div className="flex flex-col gap-3">
          {loading && <StatusMessage>正在读取原文…</StatusMessage>}
          {error && <StatusMessage tone="error">{error}</StatusMessage>}
          {body !== null && (
            <>
              <p className="ui-field-hint">
                {`引用的是 ${Array.from(body).length} 字符的那一版原文；灵感改动后不会自动换版本。`}
              </p>
              <pre className="idea-reference-body">{body}</pre>
              {view && view.revision_id !== reference.revision_id && (
                <p className="ui-field-hint">
                  这条灵感已有更新的版本。需要新内容时由你决定是否替换这次引用。
                </p>
              )}
            </>
          )}
          <div className="ui-action-list">
            <Button variant="danger" onClick={() => { remove(reference.idea_id); setOpen(false); }}>
              移除引用
            </Button>
            {view && view.revision_id !== reference.revision_id && (
              <Button onClick={() => void updateToLatest()}>更新到最新版本</Button>
            )}
          </div>
        </div>
      </AppDialog>
    </>
  );
}
