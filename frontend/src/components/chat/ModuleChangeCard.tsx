/**
 * 一轮里的模块变更结果（0.8）。
 *
 * 只投影事件里的实际结果：准备了哪些内容、哪些已经保存、哪些已经用于本作品。
 * 名称入口打开已有阅读区；继续保存与撤销走同一套服务接口，前端不根据回复正文猜成功。
 */

import type { ChatTurn, TurnModuleChange } from "../../state/chatMessages";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";

const STATUS_LABEL: Record<TurnModuleChange["status"], string> = {
  prepared: "内容已准备，尚未用于本作品",
  applied: "已用于本作品",
  failed: "保存没有完成",
  reverted: "已撤销本次修改",
};

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
  if (turn.moduleChanges.length === 0) return null;

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
                    {item.bindingId && workspaceId && change.status === "applied" ? (
                      <button
                        type="button"
                        className="text-blue underline-offset-2 hover:underline"
                        onClick={() => select(workspaceId, item.bindingId!)}
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
    </div>
  );
}
