/**
 * 右侧账本图谱概览（V03-6.4）：视图选择 + 与当前工作相关的小图 + 「展开查看」。
 *
 * 设计依据（产品 §5.4）：右侧不塞整个作品的几十个节点，只给摘要与小图；点「展开
 * 查看」把同一视图在主区打开。选中对象与布局位置都记在本地区（state/graphLayout），
 * 返回正文/讨论后再次打开仍停在上次查看处，且不产生任何领域写入。
 */

import { useEffect, useState } from "react";

import { Empty, ErrorText } from "../Workbench";
import GraphCanvas from "./GraphCanvas";
import GraphViewTabs from "./GraphViewTabs";
import { useGraphData } from "./useGraphData";
import { readSelection, writeSelection, type GraphViewId } from "../../state/graphLayout";

export default function GraphOverview({
  projectId,
  viewId,
  onSwitchView,
  onExpand,
  refreshKey = 0,
}: {
  projectId: string;
  viewId: GraphViewId;
  onSwitchView(viewId: GraphViewId): void;
  onExpand(viewId: GraphViewId): void;
  refreshKey?: number;
}) {
  const { data, loading, error } = useGraphData(projectId, viewId, refreshKey);
  const [selected, setSelected] = useState<string | null>(() =>
    readSelection(projectId, viewId),
  );

  useEffect(() => {
    setSelected(readSelection(projectId, viewId));
  }, [projectId, viewId]);

  const nodes = data?.nodes ?? [];
  const edges = data?.edges ?? [];
  const needsCheck = [...nodes, ...edges].filter(
    (item) => item.evidence_status === "needs_check",
  ).length;

  /** 侧栏选中对象：记住定位并在主区展开同一视图继续查看。 */
  function open(id: string) {
    writeSelection(projectId, viewId, id);
    setSelected(id);
    onExpand(viewId);
  }

  return (
    <section className="panel" aria-label="关联图谱">
      <header className="panel-head flex items-center justify-between px-4 py-3">
        <span>关联图谱</span>
        {data && (
          <span className="text-[10px] text-sub">
            {nodes.length} 对象 · {edges.length} 关联
          </span>
        )}
      </header>
      <div className="px-3 py-2">
        <GraphViewTabs viewId={viewId} onSwitch={onSwitchView} />
      </div>
      {error && <ErrorText>{error}</ErrorText>}
      {loading ? (
        <p className="px-4 py-3 text-xs text-sub">正在读取图谱…</p>
      ) : (
        <>
          {needsCheck > 0 && (
            <p className="px-4 pb-1 text-[10px] text-amber-600">{needsCheck} 处来源需核对</p>
          )}
          {nodes.length === 0 ? (
            <Empty>
              {viewId === "characters"
                ? "登记人物与关系后，这里会出现关系小图。"
                : viewId === "method"
                  ? "在账本新建「真相」条目并填写手法后，这里会出现手法结构。"
                  : viewId === "timeline"
                    ? "在账本登记时间线事件后，这里会出现角色/地点分轨。"
                    : "登记线索及其指向后，这里会出现线索小图。"}
            </Empty>
          ) : viewId === "timeline" && data?.timeline ? (
            <ul className="px-4 py-1 text-[11px]">
              {data.timeline.tracks.slice(0, 5).map((track) => (
                <li key={track.key} className="truncate">
                  {track.label} · {track.event_ids.length} 个事件
                </li>
              ))}
              {data.timeline.testimonies.length > 0 && (
                <li className="text-sub">
                  证词 / 认知{" "}
                  {data.timeline.testimonies.reduce((sum, item) => sum + item.items.length, 0)} 条
                </li>
              )}
            </ul>
          ) : (
            <div className="px-2">
              <GraphCanvas
                projectId={projectId}
                viewId={viewId}
                nodes={nodes}
                edges={edges}
                selectedId={selected}
                onSelectNode={open}
                onSelectEdge={() => onExpand(viewId)}
                compact
              />
            </div>
          )}
          <div className="px-4 py-2">
            <button
              className="text-[11px] text-blue underline"
              onClick={() => onExpand(viewId)}
            >
              展开查看
            </button>
          </div>
        </>
      )}
    </section>
  );
}
