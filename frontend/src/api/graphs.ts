/**
 * 图谱只读客户端（V03-6.4）：统一投影、人物图、线索图与「来源定位」证据查询。
 *
 * 设计依据（计划 M12、产品 §5.4/§5.8）：图谱固定到读取的 Commit，前端只读回显，
 * 没有写端点——拖动只改本地布局偏好（见 state/graphLayout.ts）。查询参数有值才发，
 * 空值让后端按缺省口径（全知视角、全图）处理，不在前端猜默认值。
 */

import { projectPrefix, request } from "./v2";
import type { EvidenceLinkWire } from "./history";

export interface GraphSourceRefWire {
  kind: string;
  id: string;
  revision: string;
}

export interface GraphNodeWire {
  id: string;
  kind: string;
  kind_label: string;
  title: string;
  summary: string;
  revision: string;
  semantic_layer: string;
  evidence_status: string;
  source_refs: GraphSourceRefWire[];
}

export interface GraphEdgeWire {
  id: string;
  source: string;
  target: string;
  label: string;
  relation_type: string;
  semantic_layer: string;
  evidence_status: string;
  declared_by: string | null;
  source_refs: GraphSourceRefWire[];
  /** 人物图边：关系状态；角色视角隐藏真实状态。 */
  public_state?: string;
  true_state?: string;
  true_state_hidden?: boolean;
}

export interface GraphPositionWire {
  kind: "chapter" | "beat";
  at_chapter_id: string | null;
  at_beat_id: string | null;
  label: string;
}

export interface GraphPerspectiveWire {
  mode: string;
  character_id: string | null;
  label: string;
}

export interface GraphFocusWire {
  node_id: string;
  neighbor_ids: string[];
}

export interface GraphWire {
  project_id: string;
  commit_id: string | null;
  nodes: GraphNodeWire[];
  edges: GraphEdgeWire[];
}

export interface CharacterGraphWire extends GraphWire {
  perspective: GraphPerspectiveWire;
  position: GraphPositionWire | null;
  focus: GraphFocusWire | null;
}

export type ClueStatus = "implemented" | "planned" | "missing";

export interface ClueStateWire {
  clue_id: string;
  statement: string;
  necessity: boolean;
  points_to: string[];
  planned_beats: string[];
  disclosed_beats: string[];
  status: ClueStatus;
  first_position: number | null;
  within_position: boolean | null;
}

export interface ClueGraphWire extends GraphWire {
  position: GraphPositionWire | null;
  focus: GraphFocusWire | null;
  clues: ClueStateWire[];
}

export interface GraphQuery {
  at_commit_id?: string | null;
  at_beat_id?: string | null;
  at_chapter_id?: string | null;
  focus?: string | null;
}

export interface CharacterGraphQuery extends GraphQuery {
  perspective?: string | null;
}

function queryString(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(name, value);
  }
  const encoded = search.toString();
  return encoded === "" ? "" : `?${encoded}`;
}

export async function fetchGraph(
  projectId: string,
  query: GraphQuery = {},
): Promise<GraphWire> {
  return request<GraphWire>(
    `${projectPrefix(projectId)}/graphs${queryString({ at_commit_id: query.at_commit_id })}`,
  );
}

export async function fetchCharacterGraph(
  projectId: string,
  query: CharacterGraphQuery = {},
): Promise<CharacterGraphWire> {
  const suffix = queryString({
    at_commit_id: query.at_commit_id,
    perspective: query.perspective,
    at_beat_id: query.at_beat_id,
    at_chapter_id: query.at_chapter_id,
    focus: query.focus,
  });
  return request<CharacterGraphWire>(
    `${projectPrefix(projectId)}/graphs/characters${suffix}`,
  );
}

export async function fetchClueGraph(
  projectId: string,
  query: GraphQuery = {},
): Promise<ClueGraphWire> {
  const suffix = queryString({
    at_commit_id: query.at_commit_id,
    at_beat_id: query.at_beat_id,
    at_chapter_id: query.at_chapter_id,
    focus: query.focus,
  });
  return request<ClueGraphWire>(`${projectPrefix(projectId)}/graphs/clues${suffix}`);
}

/**
 * 来源定位：按被锚定的账本对象回读它在正文中的证据（章 + 引用片段 + 核对状态）。
 * 走既有 `/evidence-links` 端点，只加 `target_id` 过滤，不新增读模型。
 */
export async function fetchEvidenceByTarget(
  projectId: string,
  targetId: string,
): Promise<EvidenceLinkWire[]> {
  const body = await request<{ evidence: EvidenceLinkWire[] }>(
    `${projectPrefix(projectId)}/evidence-links${queryString({ target_id: targetId })}`,
  );
  return body.evidence;
}
