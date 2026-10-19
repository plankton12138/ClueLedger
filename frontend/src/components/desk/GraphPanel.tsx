/**
 * 主区图谱面板（V03-6.4）：右侧局部图「展开查看」后在主区打开的同一视图。
 *
 * 设计依据（产品 §5.4/§5.8）：主区一次只展开一个视图（复用 detail 主区槽位），
 * 底部仍保留唯一输入框（在壳层，不在本组件内）。点节点看条目、点连线看关系含义，
 * 从来源定位正文、把对象加入讨论；拖动只改布局偏好。返回正文/讨论时图谱定位
 * 由本地偏好记住，再次打开可继续查看。
 */

import { useEffect, useMemo, useState } from "react";

import { Empty, ErrorText } from "../Workbench";
import GraphCanvas from "./GraphCanvas";
import GraphDetail from "./GraphDetail";
import GraphDirectory from "./GraphDirectory";
import GraphViewTabs from "./GraphViewTabs";
import { useGraphData } from "./useGraphData";
import { readSelection, writeSelection, type GraphViewId } from "../../state/graphLayout";
import {
  buildDirectory,
  graphEmptyText,
  readGraphMode,
  rowKey,
  writeGraphMode,
  type GraphMode,
} from "../../state/graphDirectory";

type Selection = { type: "node" | "edge"; id: string } | null;

export default function GraphPanel({
  projectId,
  viewId,
  onSwitchView,
  onNavigateChapter,
  onAddToDiscussion,
  chapterTitles,
  refreshKey = 0,
}: {
  projectId: string;
  viewId: GraphViewId;
  onSwitchView(viewId: GraphViewId): void;
  onNavigateChapter(chapterId: string): void;
  onAddToDiscussion(reference: string): void;
  chapterTitles: Record<string, string>;
  refreshKey?: number;
}) {
  const { data, loading, error } = useGraphData(projectId, viewId, refreshKey);
  const [selection, setSelection] = useState<Selection>(() => {
    const saved = readSelection(projectId, viewId);
    return saved ? { type: "node", id: saved } : null;
  });
  // 查看方式偏好（画布/文字目录）与布局偏好同属本地区，按 project + view 隔离。
  const [mode, setMode] = useState<GraphMode>(() => readGraphMode(projectId, viewId));

  // 换作品/换视图：重读本地记住的定位（产品 §5.4「再次打开可继续查看」）。
  useEffect(() => {
    const saved = readSelection(projectId, viewId);
    setSelection(saved ? { type: "node", id: saved } : null);
    setMode(readGraphMode(projectId, viewId));
  }, [projectId, viewId]);

  const nodesById = useMemo(
    () => Object.fromEntries((data?.nodes ?? []).map((node) => [node.id, node])),
    [data],
  );
  const groups = useMemo(
    () =>
      buildDirectory({
        nodes: data?.nodes ?? [],
        edges: data?.edges ?? [],
        clues: data?.clues ?? [],
      }),
    [data],
  );

  function switchMode(next: GraphMode) {
    setMode(next);
    writeGraphMode(projectId, viewId, next);
  }

  function selectNode(id: string) {
    setSelection({ type: "node", id });
    writeSelection(projectId, viewId, id);
  }
  function selectEdge(id: string) {
    setSelection({ type: "edge", id });
  }

  const selectedNode = selection?.type === "node" ? nodesById[selection.id] ?? null : null;
  const selectedEdge =
    selection?.type === "edge"
      ? data?.edges.find((edge) => edge.id === selection.id) ?? null
      : null;
  const clue = selectedNode
    ? data?.clues.find((item) => item.clue_id === selectedNode.id) ?? null
    : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <GraphViewTabs viewId={viewId} onSwitch={onSwitchView} />
        <div className="flex items-center gap-1 text-[11px]" role="group" aria-label="查看方式">
          {(["canvas", "directory"] as const).map((item) => (
            <button
              key={item}
              aria-pressed={mode === item}
              className={`rounded px-2 py-0.5 ${
                mode === item ? "bg-blue-50 text-blue-700" : "text-sub hover:text-fgStrong"
              }`}
              onClick={() => switchMode(item)}
            >
              {item === "canvas" ? "画布" : "文字目录"}
            </button>
          ))}
        </div>
        {data && (
          <span className="ml-auto text-[10px] text-sub">
            {[data.perspectiveLabel, data.positionLabel].filter(Boolean).join(" · ")}
            {data.nodes.length > 0 && ` · ${data.nodes.length} 个对象 / ${data.edges.length} 条关联`}
          </span>
        )}
      </div>
      {error && <ErrorText>{error}</ErrorText>}
      {loading ? (
        <Empty>正在读取图谱…</Empty>
      ) : data ? (
        <>
          <div className="flex min-h-0 flex-1 flex-col">
            {mode === "canvas" ? (
              <GraphCanvas
                projectId={projectId}
                viewId={viewId}
                nodes={data.nodes}
                edges={data.edges}
                selectedId={selection?.id ?? null}
                onSelectNode={selectNode}
                onSelectEdge={selectEdge}
              />
            ) : (
              <GraphDirectory
                groups={groups}
                selectedKey={selection ? rowKey(selection.type, selection.id) : null}
                emptyText={graphEmptyText(viewId)}
                onSelect={(row) => {
                  if (row.kind === "node") selectNode(row.id);
                  else selectEdge(row.id);
                }}
              />
            )}
          </div>
          <GraphDetail
            projectId={projectId}
            node={selectedNode}
            edge={selectedEdge}
            nodesById={nodesById}
            clue={clue}
            chapterTitles={chapterTitles}
            onAddToDiscussion={onAddToDiscussion}
            onNavigateChapter={onNavigateChapter}
          />
        </>
      ) : null}
    </div>
  );
}
