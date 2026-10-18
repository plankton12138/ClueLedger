/**
 * 图谱视图切换标签（V03-6.4）：人物关系 / 线索关联；手法结构与时间线占位。
 *
 * 设计依据（产品 §5.4）：右侧账本提供「目录 / 人物关系 / 线索关联 / 手法结构 /
 * 时间线」的视图选择。0.3 阶段手法结构与时间线只投影正式数据，前端先给占位，
 * 不做「等填满的大画布」。
 */

import type { GraphViewId } from "../../state/graphLayout";

export const GRAPH_VIEWS: { id: GraphViewId; label: string }[] = [
  { id: "characters", label: "人物关系" },
  { id: "clues", label: "线索关联" },
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
      <span className="ml-1 text-[10px] text-sub" title="0.3 只投影正式账本数据">
        手法结构 · 时间线（V03-7）
      </span>
    </div>
  );
}
