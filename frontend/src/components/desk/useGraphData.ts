/**
 * 图谱数据取数（V03-6.4）：把人物图/线索图两种响应归一到同一形状，供侧栏概览
 * 与主区面板共用。只读，固定到读取的 Commit；失败不吞错，交给调用方显示。
 */

import { useEffect, useState } from "react";

import {
  fetchCharacterGraph,
  fetchClueGraph,
  type ClueStateWire,
  type GraphEdgeWire,
  type GraphNodeWire,
} from "../../api/graphs";
import { message } from "../../state/v2";
import type { GraphViewId } from "../../state/graphLayout";

export interface GraphViewData {
  commitId: string | null;
  nodes: GraphNodeWire[];
  edges: GraphEdgeWire[];
  clues: ClueStateWire[];
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
            perspectiveLabel: graph.perspective?.label ?? null,
            positionLabel: graph.position?.label ?? null,
          }))
        : fetchClueGraph(projectId).then((graph) => ({
            commitId: graph.commit_id,
            nodes: graph.nodes,
            edges: graph.edges,
            clues: graph.clues,
            perspectiveLabel: null,
            positionLabel: graph.position?.label ?? null,
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
