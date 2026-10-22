/**
 * 本轮修改范围（0.6 V06-B2）。
 *
 * 说明模型登记了什么权限：任务理解、作者授权原话、允许改的确切路径、明确禁止项。
 * 它是「应用允许了哪些操作」的记录，不是「模型理解正确」的证明。
 */

import type { TaskGrant, TaskScope } from "../../api/workspace";

const ACTION_LABEL: Record<TaskGrant["action"], string> = {
  create: "新建",
  update: "修改",
  delete: "删除",
  move: "移动",
};

function grantLabel(grant: TaskGrant): string {
  if (grant.action === "move" && grant.target) {
    return `${ACTION_LABEL[grant.action]} ${grant.path} → ${grant.target}`;
  }
  return `${ACTION_LABEL[grant.action]} ${grant.path}`;
}

interface Props {
  scope: TaskScope;
}

export default function TaskScopeDetails({ scope }: Props) {
  const title =
    scope.mode === "read_only" ? "本轮只读" : `本轮范围 v${scope.version} · ${scope.grants.length} 项`;
  return (
    <details className="mt-1 text-[11px] text-sub">
      <summary className="cursor-pointer text-[10px] text-mute">{title}</summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        <li>任务：{scope.summary}</li>
        <li className="truncate">授权原话：{scope.authorization}</li>
        {scope.carried_from_seq !== null && (
          <li className="text-mute">承接第 {scope.carried_from_seq} 条作者消息</li>
        )}
        {scope.grants.map((grant) => (
          <li key={`${grant.action}:${grant.path}:${grant.target ?? ""}`}>
            允许{grantLabel(grant)}
          </li>
        ))}
        {scope.denied_paths.length > 0 && (
          <li className="text-amber">不动：{scope.denied_paths.join("、")}</li>
        )}
        {scope.extendable && (
          <li className="text-mute">
            可按
            {scope.extend_actions
              .map((action) => ACTION_LABEL[action as TaskGrant["action"]] ?? action)
              .join("、")}
            追加相关文件
          </li>
        )}
        {scope.mode === "read_only" && <li className="text-mute">这一轮不写文件。</li>}
      </ul>
    </details>
  );
}
