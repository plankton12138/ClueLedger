/**
 * 图谱数据取数（V03-6.4 / V03-7.5 / V03-7.6）：把人物图/线索图/手法图/时间线四种响应
 * 归一到同一形状，供侧栏概览与主区面板共用。只读，固定到读取的 Commit；失败不吞错。
 */

import { useEffect, useState } from "react";

import {
  fetchCharacterGraph,
  fetchClueGraph,
  fetchMethodGraph,
  fetchTimelineGraph,
  type ClueStateWire,
  type GraphEdgeWire,
  type GraphNodeWire,
  type MethodGraphWire,
  type TimelineGraphWire,
} from "../../api/graphs";
import { message } from "../../state/v2";
import type { GraphViewId } from "../../state/graphLayout";

export interface GraphViewData {
  commitId: string | null;
  nodes: GraphNodeWire[];
  edges: GraphEdgeWire[];
  clues: ClueStateWire[];
  /** 手法图专用：真相文字、缺口与完整度；其他视图为 null。 */
  method: MethodGraphWire | null;
  /** 时间线专用：事件轨、证词轨与顺序说明；其他视图为 null。 */
  timeline: TimelineGraphWire | null;
  /** 当前视角说明（如「作者全知」），来源为空时为 null。 */
  perspectiveLabel: string | null;
  /** 当前叙述位置说明，未筛位置时为 null。 */
  positionLabel: string | null;
}

export function useGraphData(
  projectId: string,
  viewId: GraphViewId,
  refreshKey = 0,
): { data: GraphViewData | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<GraphViewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    const pending =
      viewId === "characters"
        ? fetchCharacterGraph(projectId).then((graph) => ({
            commitId: graph.commit_id,
            nodes: graph.nodes,
            edges: graph.edges as GraphEdgeWire[],
            clues: [] as ClueStateWire[],
            method: null,
            timeline: null,
            perspectiveLabel: graph.perspective?.label ?? null,
            positionLabel: graph.position?.label ?? null,
          }))
        : viewId === "clues"
          ? fetchClueGraph(projectId).then((graph) => ({
              commitId: graph.commit_id,
              nodes: graph.nodes,
              edges: graph.edges,
              clues: graph.clues,
              method: null,
              timeline: null,
              perspectiveLabel: null,
              positionLabel: graph.position?.label ?? null,
            }))
          : viewId === "method"
            ? fetchMethodGraph(projectId).then((graph) => ({
                commitId: graph.commit_id,
                nodes: graph.nodes,
                edges: graph.edges,
                clues: [] as ClueStateWire[],
                method: graph,
                timeline: null,
                perspectiveLabel: null,
                positionLabel: null,
              }))
            : fetchTimelineGraph(projectId).then((graph) => ({
                commitId: graph.commit_id,
                nodes: graph.nodes,
                edges: graph.edges,
                clues: [] as ClueStateWire[],
                method: null,
                timeline: graph,
                perspectiveLabel: null,
                positionLabel: null,
              }));
    pending
      .then((next) => {
        if (!alive) return;
        setData(next);
        setLoading(false);
      })
      .catch((e) => {
        if (!alive) return;
        setError(message(e));
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [projectId, viewId, refreshKey]);

  return { data, loading, error };
}
