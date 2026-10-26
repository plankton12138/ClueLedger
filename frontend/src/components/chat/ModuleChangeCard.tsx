/**
 * 一轮里的模块变更结果（0.8）。
 *
 * 只投影事件里的实际结果：准备了哪些内容、哪些已经保存、哪些已经用于本作品。
 * 应用前的名称打开本次准备内容的预览（正文或逐条修改），应用后的名称打开已有阅读区；
 * 继续保存与撤销走同一套服务接口，前端不根据回复正文猜成功。
 */

import { useState } from "react";

import { errorMessage } from "../../api/errors";
import { fetchModuleChangeSet, type ModuleChangeSetView } from "../../api/moduleLibrary";
import type { ChatTurn, TurnModuleChange } from "../../state/chatMessages";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { AppDialog } from "../ui/AppDialog";
import ChatMarkdown from "./ChatMarkdown";
import { findChangePreview } from "./changePreview";

const STATUS_LABEL: Record<TurnModuleChange["status"], string> = {
  prepared: "内容已准备，尚未用于本作品",
  applied: "已用于本作品",
  failed: "保存没有完成",
  reverted: "已撤销本次修改",
  superseded: "已由同轮后续修订取代",
};

interface PreviewState {
  changeSetId: string;
  operationId: string;
  view: ModuleChangeSetView | null;
  loading: boolean;
  error: string;
}

function operationNames(change: TurnModuleChange): { id: string; label: string; bindingId: string | null }[] {
  return change.operations.map((operation) => {
    const saved = operation.saved ?? change.saved.find((item) => item.operation_id === operation.operation_id);
    return {
      id: operation.operation_id,
      label: saved?.name || operation.name || operation.operation_id,
      bindingId: saved?.binding_id ?? null,
    };
  });
}

interface Props {
  turn: ChatTurn;
  workspaceId: string | null;
  conversationId: string | null;
}

export default function ModuleChangeCard({ turn, workspaceId, conversationId }: Props) {
  const busy = useWorkspaceModulesStore((state) => state.busy);
  const message = useWorkspaceModulesStore((state) => state.message);
  const select = useWorkspaceModulesStore((state) => state.select);
  const applyChangeSet = useWorkspaceModulesStore((state) => state.applyChangeSet);
  const undoChangeSet = useWorkspaceModulesStore((state) => state.undoChangeSet);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  if (turn.moduleChanges.length === 0) return null;

  const openPreview = async (changeSetId: string, operationId: string) => {
    if (!workspaceId) return;
    if (preview?.changeSetId === changeSetId && preview.view) {
      setPreview({ ...preview, operationId });
      return;
    }
    setPreview({ changeSetId, operationId, view: null, loading: true, error: "" });
    try {
      const view = await fetchModuleChangeSet(workspaceId, changeSetId);
      setPreview((state) =>
        state && state.changeSetId === changeSetId ? { ...state, view, loading: false } : state,
      );
    } catch (error) {
      setPreview((state) =>
        state && state.changeSetId === changeSetId
          ? { ...state, loading: false, error: errorMessage(error, "读取准备内容") }
          : state,
      );
    }
  };

  const detail =
    preview?.view && !preview.loading ? findChangePreview(preview.view, preview.operationId) : null;

  return (
    <div className="mt-1 flex flex-col gap-1 text-[11px]" data-testid="module-change-card">
      {turn.moduleChanges.map((change) => {
        const names = operationNames(change);
        const canApply = workspaceId !== null && (change.status === "prepared" || change.status === "failed");
        const canUndo = workspaceId !== null && change.status === "applied";
        return (
          <div key={change.changeSetId} className="rounded border border-line bg-surface px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className={change.status === "failed" ? "font-bold text-amber" : "font-bold"}>
                {STATUS_LABEL[change.status]}
              </span>
              {change.status === "reverted" && change.note && (
                <span className="text-mute">{change.note}</span>
              )}
            </div>
            {names.length > 0 && (
              <ul className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
                {names.map((item) => (
                  <li key={item.id}>
                    {workspaceId && change.status === "applied" && item.bindingId ? (
                      <button
                        type="button"
                        className="text-blue underline-offset-2 hover:underline"
                        onClick={() => select(workspaceId, item.bindingId!)}
                      >
                        {item.label}
                      </button>
                    ) : workspaceId && change.status !== "superseded" ? (
                      <button
                        type="button"
                        className="text-blue underline-offset-2 hover:underline"
                        title="查看这次准备的内容"
                        onClick={() => void openPreview(change.changeSetId, item.id)}
                      >
                        {item.label}
                      </button>
                    ) : (
                      <span>{item.label}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {change.error && <p className="mt-1 text-amber">{change.error}</p>}
            {change.stage === "assembly" && (
              <p className="mt-1 text-amber">内容已经保存，当前作品装配没有完成；可以继续保存。</p>
            )}
            <div className="mt-1 flex flex-wrap gap-2">
              {canApply && (
                <button
                  type="button"
                  className="btn btn-gray px-2 py-0.5 text-[10px]"
                  disabled={busy !== null}
                  onClick={() =>
                    void applyChangeSet(
                      workspaceId!,
                      change.changeSetId,
                      change.digest,
                      conversationId ?? undefined,
                    )
                  }
                >
                  继续保存
                </button>
              )}
              {canUndo && (
                <button
                  type="button"
                  className="btn btn-gray px-2 py-0.5 text-[10px]"
                  disabled={busy !== null}
                  onClick={() =>
                    void undoChangeSet(
                      workspaceId!,
                      change.changeSetId,
                      change.digest,
                      conversationId ?? undefined,
                    )
                  }
                >
                  撤销本次修改
                </button>
              )}
            </div>
            {message && message.workspaceId === workspaceId && (
              <p className={message.error ? "mt-1 text-amber" : "mt-1 text-sub"}>{message.text}</p>
            )}
          </div>
        );
      })}

      <AppDialog
        open={preview !== null}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
        title="内容预览"
      >
        {preview?.loading && <p className="text-sub">正在读取准备内容…</p>}
        {preview?.error && <p className="text-amber">{preview.error}</p>}
        {detail && (
          <div className="flex flex-col gap-2 text-[12px]" data-testid="change-preview">
            <div className="flex flex-wrap items-center gap-1 text-mute">
              <span>{detail.kind === "create" ? "新建内容" : "修改内容"}</span>
              <span>·</span>
              <span className="font-bold text-sub">{detail.title}</span>
              {detail.body && <span>· {detail.body.length} 字</span>}
            </div>
            {detail.kind === "create" ? (
              <ChatMarkdown text={detail.body} wide />
            ) : (
              detail.edits.map((edit) => (
                <div key={edit.entryId} className="rounded border border-line px-2 py-1.5">
                  <p className="text-mute">条目：{edit.entryId}</p>
                  {edit.body ? (
                    <ChatMarkdown text={edit.body} wide />
                  ) : (
                    <>
                      <p className="mt-1 text-mute">原文</p>
                      <pre className="whitespace-pre-wrap">{edit.find}</pre>
                      <p className="mt-1 text-mute">改为</p>
                      <pre className="whitespace-pre-wrap">{edit.replace}</pre>
                    </>
                  )}
                </div>
              ))
            )}
            {preview?.view?.status === "prepared" && (
              <p className="text-mute">内容已保存为任务进度，尚未用于当前作品。</p>
            )}
          </div>
        )}
      </AppDialog>
    </div>
  );
}

