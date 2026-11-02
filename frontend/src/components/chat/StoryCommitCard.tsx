/**
 * 构思提交回执：本轮真正保存了什么、有哪些对象、可以撤销。
 *
 * 回执只来自 `story_changes_committed` 事件；展开时才读取改动记录。撤销走同一次
 * 结构提交的逆操作，失败时保留事实，不在界面上先装作成功。
 */

import { useState } from "react";
import { ChevronDown, Undo2 } from "lucide-react";

import { readStoryChange, undoStoryChange } from "../../api/story";
import { errorMessage } from "../../api/errors";
import { useWritingStore } from "../../state/writing";
import type { TurnStoryCommit } from "../../state/chatMessages";

function operationLabel(operation: Record<string, unknown>): string {
  const op = String(operation.op ?? "变更");
  const target =
    (typeof operation.node_id === "string" && operation.node_id) ||
    (typeof operation.clue_id === "string" && operation.clue_id) ||
    (typeof operation.constraint_id === "string" && operation.constraint_id) ||
    (typeof operation.stage_id === "string" && operation.stage_id) ||
    (typeof operation.thread_id === "string" && operation.thread_id) ||
    "";
  return target ? `${op} · ${target}` : op;
}

function CommitRow({
  commit,
  workspaceId,
}: {
  commit: TurnStoryCommit;
  workspaceId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [operations, setOperations] = useState<string[] | null>(null);
  const [undone, setUndone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (commit.noChange) {
    return (
      <p className="chat-note">
        构思结构：已核对，无需改动{commit.reason ? `（${commit.reason}）` : ""}
      </p>
    );
  }

  async function expand() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (operations || !workspaceId) return;
    try {
      const record = await readStoryChange(workspaceId, commit.changeId);
      setOperations(record.operations.map(operationLabel));
      if (record.undone_by) setUndone(true);
    } catch (cause) {
      setError(errorMessage(cause, "读取改动记录"));
    }
  }

  async function undo() {
    if (!workspaceId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await undoStoryChange(
        workspaceId,
        commit.changeId,
        `story-undo-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      );
      setUndone(true);
      const writing = useWritingStore.getState();
      if (writing.workspaceId === workspaceId) void writing.refresh();
    } catch (cause) {
      setError(errorMessage(cause, "撤销这次调整"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-story-commit" data-change-id={commit.changeId}>
      <div className="chat-story-commit-head">
        <span className="font-bold">
          {undone ? "构思调整已撤销" : `构思已保存 ${commit.operations} 项`}
        </span>
        {!undone && (
          <button type="button" className="btn btn-gray px-2 py-0.5 text-[10px]" onClick={() => void undo()} disabled={busy}>
            <Undo2 size={12} aria-hidden="true" />
            {busy ? "撤销中…" : "撤销这次调整"}
          </button>
        )}
        <button type="button" className="btn btn-gray px-2 py-0.5 text-[10px]" onClick={() => void expand()} aria-expanded={open}>
          <ChevronDown size={12} aria-hidden="true" />
          {open ? "收起改动" : "查看改动"}
        </button>
      </div>
      {commit.labels.length > 0 && (
        <p className="chat-note">{commit.labels.join("、")}</p>
      )}
      {open && (
        <ul className="chat-story-commit-list">
          {operations === null ? (
            <li className="text-mute">正在读取改动…</li>
          ) : operations.length === 0 ? (
            <li className="text-mute">这次改动只涉及正文或没有可列出的结构操作。</li>
          ) : (
            operations.map((label, index) => <li key={`${label}:${index}`}>{label}</li>)
          )}
        </ul>
      )}
      {error && <p className="chat-error">{error}</p>}
    </div>
  );
}

export default function StoryCommitCard({
  commits,
  workspaceId,
}: {
  commits: TurnStoryCommit[];
  workspaceId: string | null;
}) {
  if (commits.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      {commits.map(commit => (
        <CommitRow key={commit.changeId} commit={commit} workspaceId={workspaceId} />
      ))}
    </div>
  );
}
