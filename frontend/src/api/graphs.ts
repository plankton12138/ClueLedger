/**
 * 图谱只读客户端（V03-6.4 / V03-7.5 / V03-7.6）：统一投影、人物图、线索图、
 * 手法图、时间线与「来源定位」证据查询。
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

/** 手法图（V03-7.5）：只回显账本已有文字与关系，缺口逐条给出。 */
export interface MethodTruthWire {
  truth_id: string;
  culprit_id: string;
  motive: string;
  method: string;
  cover_up: string;
  accomplice_ids: string[];
  true_timeline: string[];
  missing_fields: string[];
}

export interface MethodGapWire {
  code: string;
  where: string;
  why: string;
  fix: string;
  truth_id: string | null;
}

export interface MethodCompletenessWire {
  recorded: number;
  total: number;
  /** 明确「只统计三段文字是否已写」，不代表推理链成立。 */
  note: string;
}

export interface MethodGraphWire extends GraphWire {
  focus: GraphFocusWire | null;
  truths: MethodTruthWire[];
  gaps: MethodGapWire[];
  completeness: MethodCompletenessWire;
}

/** 时间线（V03-7.6）：真实事件分轨 + 证词/认知分轨；时间原文、不排序。 */
export interface TimelineEventWire {
  event_id: string;
  time_text: string;
  time_status: "recorded" | "unspecified";
  place_id: string;
  participants: string[];
  before_state: string;
  after_state: string;
}

export interface TimelineTrackWire {
  key: string;
  kind: "character" | "place";
  id: string;
  label: string;
  event_ids: string[];
}

export interface TestimonyItemWire {
  knowledge_id: string;
  character_id: string;
  fact_id: string;
  mode: string;
  mode_label: string;
  note: string;
  at_beat_id: string | null;
  position: number | null;
  claimed_event_id: string | null;
  claimed_event_time: string;
}

export interface TestimonyTrackWire {
  character_id: string;
  label: string;
  items: TestimonyItemWire[];
}

export interface TimelineGapWire {
  code: string;
  where: string;
  why: string;
  fix: string;
  target_id: string | null;
}

export interface TimelineOrderingWire {
  story_time: string;
  narrative_order: string;
  note: string;
}

export interface TimelineGraphWire extends GraphWire {
  group_by: "character" | "place";
  ordering: TimelineOrderingWire;
  testimony_note: string;
  events: TimelineEventWire[];
  tracks: TimelineTrackWire[];
  testimonies: TestimonyTrackWire[];
  gaps: TimelineGapWire[];
  focus: GraphFocusWire | null;
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

export async function fetchMethodGraph(
  projectId: string,
  query: GraphQuery = {},
): Promise<MethodGraphWire> {
  const suffix = queryString({
    at_commit_id: query.at_commit_id,
    focus: query.focus,
  });
  return request<MethodGraphWire>(`${projectPrefix(projectId)}/graphs/method${suffix}`);
}

export interface TimelineGraphQuery extends GraphQuery {
  group_by?: "character" | "place" | null;
}

export async function fetchTimelineGraph(
  projectId: string,
  query: TimelineGraphQuery = {},
): Promise<TimelineGraphWire> {
  const suffix = queryString({
    at_commit_id: query.at_commit_id,
    group_by: query.group_by,
    focus: query.focus,
  });
  return request<TimelineGraphWire>(`${projectPrefix(projectId)}/graphs/timeline${suffix}`);
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
