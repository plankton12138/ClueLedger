/**
 * 当前资料任务条（0.7）。
 *
 * 只显示服务端确认过的进度：范围、模型、已处理目标数。不显示估算百分比，也不把
 * 模型回复当成步骤。任务可以在切页后继续，回到资料页时从这里接上。
 */

import type { MaterialTask } from "../../api/storyMaterials";
import { TASK_LABEL } from "./labels";

interface Props {
  task: MaterialTask | null;
  onStop: () => void;
  onOpenActivity: () => void;
}

export default function MaterialTaskBar({ task, onStop, onOpenActivity }: Props) {
  if (!task) return null;
  const running = task.status === "accepted" || task.status === "running";
  return (
    <div
      className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-panel-edge bg-ui-selected px-3 py-1.5 text-sm"
      role="status"
    >
      <span className="font-bold">{task.label}</span>
      <span className="text-sub">
        {task.total > 0 ? `已处理 ${task.done}/${task.total}` : "准备中"}
      </span>
      <span className="text-mute">{TASK_LABEL[task.status]}</span>
      {task.model && <span className="text-mute">模型 {task.model}</span>}
      <div className="flex-1" />
      <button className="underline text-blue" onClick={onOpenActivity}>
        查看活动记录
      </button>
      {running && (
        <button className="btn btn-gray px-2 py-0.5 text-sm" onClick={onStop}>
          停止
        </button>
      )}
    </div>
  );
}
