/**
 * 图谱视图切换标签（V03-6.4 / V03-7.5 / V03-7.6）：人物关系 / 线索关联 /
 * 手法结构 / 时间线。
 *
 * 设计依据（产品 §5.4）：右侧账本提供「目录 / 人物关系 / 线索关联 / 手法结构 /
 * 时间线」的视图选择。手法结构只回显账本已有文字与关系；时间线按角色或地点分轨，
 * 真实事件与证词分开，缺口如实显示。
 */

import type { GraphViewId } from "../../state/graphLayout";

export const GRAPH_VIEWS: { id: GraphViewId; label: string }[] = [
  { id: "characters", label: "人物关系" },
  { id: "clues", label: "线索关联" },
  { id: "method", label: "手法结构" },
  { id: "timeline", label: "时间线" },
];

export default function GraphViewTabs({
  viewId,
  onSwitch,
}: {
  viewId: GraphViewId;
  onSwitch(viewId: GraphViewId): void;
}) {
  return (
    <div className="flex items-center gap-1 text-[11px]" role="tablist" aria-label="图谱视图">
      {GRAPH_VIEWS.map((view) => (
        <button
          key={view.id}
          role="tab"
          aria-selected={view.id === viewId}
          className={`rounded px-2 py-0.5 ${
            view.id === viewId ? "bg-blue-50 text-blue-700" : "text-sub hover:text-fgStrong"
          }`}
          onClick={() => onSwitch(view.id)}
        >
          {view.label}
        </button>
      ))}
    </div>
  );
}
