/**
 * 构思结构、时间轴与提醒的作品接口。
 *
 * 与后端 `workspace/story_api.py` 一一对应：读取方案、图、时间轴与三状态，提交确定性
 * 变更，回看与撤销。这里不做语义推断，也不在客户端拼 ID——所有标识来自服务端。
 */

import { request } from "./workspace";

export interface StoryCounts {
  stages: number;
  nodes: number;
  clues: number;
  threads: number;
  constraints: number;
  edges: number;
  bindings: number;
}

export interface StoryVariantSummary {
  id: string;
  name: string;
  revision: string;
  primary: boolean;
  base_variant_id: string | null;
  base_revision: string | null;
  counts: StoryCounts;
}

export interface StoryVariantList {
  revision: string;
  primary_variant_id: string;
  variants: StoryVariantSummary[];
}

export interface StoryStage {
  id: string;
  title: string;
  order: number;
  summary: string | null;
}

export interface StoryThread {
  id: string;
  title: string;
  order: number;
  goal: string | null;
  obstacle: string | null;
  intended_resolution: string | null;
}

export interface StoryEventSpan {
  start_minute: number;
  end_minute: number;
  approximate: boolean;
}

export interface StoryNode {
  id: string;
  title: string;
  action: string;
  change: string | null;
  stage_id: string | null;
  thread_ids: string[];
  clue_ids: string[];
  primary_thread_id?: string | null;
  is_convergence?: boolean;
  reveal_order?: number | null;
  time_note: string;
  planned_chapter_number?: number | null;
  event_span?: StoryEventSpan | null;
}

export interface StoryClueUse {
  clue_id: string;
  roles: string[];
  presentation: string;
  mode: string;
  speaker_id: string;
  expected_interpretation: string;
}

export interface StoryKnowledgeChange {
  subject_id: string;
  claim: string;
  stance: "knows" | "believes";
  source_kind: "observation" | "inference";
  source_node_ids: string[];
  effective_event_node_id: string;
}

/** 节点完整字段：编辑时整份提交，不丢掉详情面板没有显示的用途与知情。 */
export interface StoryNodeFull extends StoryNode {
  clue_uses: StoryClueUse[];
  knowledge_changes: StoryKnowledgeChange[];
}

export interface StoryEdge {
  id: string;
  from_id: string;
  to_id: string;
  kind: "next" | "reframe" | "answer";
  reason: string;
}

export interface StoryGraph {
  latest_change_id?: string | null;
  variant_id: string;
  name: string;
  revision: string;
  time_origin_label?: string;
  stages: StoryStage[];
  nodes: StoryNode[];
  edges: StoryEdge[];
  threads: StoryThread[];
  event_times: { before: string; after: string }[];
  constraints: {
    id: string;
    text: string;
    strength: "required" | "preference";
    scope: { kind: string; target: string };
  }[];
  /** 片段折叠时不在当前图里的节点。 */
  collapsed: string[];
}

/** 三状态：就绪、草稿、需核对。原因单独返回。 */
export type StoryStatus = "ready" | "draft" | "attention";

export type StoryReason =
  | "review_finding"
  | "anchor_missing"
  | "anchor_ambiguous"
  | "anchor_unavailable"
  | "not_checked"
  | "plan_changed"
  | "prose_changed";

export interface StoryAttentionNode {
  node_id: string;
  title: string;
  status: StoryStatus;
  reasons: StoryReason[];
  fingerprint: string;
  reminder_visible: boolean;
  deferred_at: string;
  binding_ids: string[];
  chapter_paths: string[];
}

export interface StoryAttention {
  variant_id: string;
  variant_revision: string;
  ledger_revision: string;
  view_revision: string;
  counts: { ready: number; draft: number; attention: number; deferred: number };
  nodes: StoryAttentionNode[];
}

export interface TimelineNode {
  node_id: string;
  title: string;
  time_note: string;
  start_minute: number | null;
  end_minute: number | null;
  approximate: boolean;
  status: StoryStatus;
  reasons: string[];
  binding_ids: string[];
  chapter_paths: string[];
}

export interface TimelineGroup {
  chapter_number: number | null;
  chapter_kind: "actual" | "planned" | "unassigned";
  title: string;
  nodes: TimelineNode[];
}

export interface StoryTimeline {
  variant_id: string;
  variant_revision: string;
  ledger_revision: string;
  view_revision: string;
  time_origin_label: string;
  domain: { min: number; max: number } | null;
  groups: TimelineGroup[];
}

export type ClueKind = "truth" | "mislead";
export type ClueRelationKind = "supports" | "contradicts" | "co_misdirects";

export interface ClueRelationEdge {
  relation_id: string;
  kind: ClueRelationKind;
  from_clue_id: string;
  to_clue_id: string;
  reason: string;
  basis_source_ids: string[];
}

export interface ClueRelations {
  variant_id: string;
  variant_revision: string;
  view_revision: string;
  focus_clue_id: string | null;
  nodes: { clue_id: string; title: string; kind: ClueKind | null; relations: number }[];
  edges: ClueRelationEdge[];
  remaining_count: number;
}

export interface ProseMark {
  node_id: string;
  title: string;
  start: number;
  end: number;
  quote: string;
  state: StoryStatus | "unchecked" | string;
  source: "candidate" | "outline_from_prose" | "manual" | "inferred";
}

export interface ProseMarks {
  path: string;
  digest: string;
  marks: ProseMark[];
}

export interface StoryReceipt {
  variant_id: string;
  base_revision: string;
  revision: string;
  change_id: string;
  request_key: string;
  result: Record<string, unknown>;
  changed: string[];
}

export interface StoryChangeRecord {
  change_id: string;
  variant_id: string;
  base_revision: string;
  revision: string;
  created_at: string;
  operations: Record<string, unknown>[];
  changed: string[];
  undone_by: string | null;
}

export function listStoryVariants(workspaceId: string): Promise<StoryVariantList> {
  return request<StoryVariantList>(`/${workspaceId}/story/variants`);
}

export function readStoryVariant(
  workspaceId: string,
  variantId: string,
): Promise<StoryGraph> {
  return request<StoryGraph>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}`,
  );
}

export function readStoryNode(
  workspaceId: string,
  variantId: string,
  nodeId: string,
): Promise<StoryNodeFull> {
  return request<StoryNodeFull>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/nodes/${encodeURIComponent(nodeId)}`,
  );
}

export function readStoryTimeline(
  workspaceId: string,
  variantId: string,
): Promise<StoryTimeline> {
  return request<StoryTimeline>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/timeline`,
  );
}

export function readStoryAttention(
  workspaceId: string,
  variantId: string,
  chapterPath?: string,
): Promise<StoryAttention> {
  const query = chapterPath ? `?chapter_path=${encodeURIComponent(chapterPath)}` : "";
  return request<StoryAttention>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/sync${query}`,
  );
}

export function readClueRelations(
  workspaceId: string,
  variantId: string,
  focusClueId?: string | null,
): Promise<ClueRelations> {
  const params = new URLSearchParams({ variant_id: variantId });
  if (focusClueId) params.set("focus_clue_id", focusClueId);
  return request<ClueRelations>(`/${workspaceId}/clue-relations?${params}`);
}

export function readStoryProseMarks(
  workspaceId: string,
  variantId: string,
  chapterPath: string,
): Promise<ProseMarks> {
  return request<ProseMarks>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/prose-marks?chapter_path=${encodeURIComponent(chapterPath)}`,
  );
}

/** 界面发起的确定性变更：修订不符返回 409，同键同负载只应用一次。 */
export function submitStoryChange(
  workspaceId: string,
  body: {
    variant_id: string;
    expected_revision: string;
    request_key: string;
    operations: Record<string, unknown>[];
    prose_patches?: { path: string; base_digest: string; start: number; end: number; expected: string; replacement: string; delete?: boolean }[];
    read_dependencies?: { kind: "variant" | "prose" | "constraint" | "module" | "conversation"; id: string; revision: string }[];
    note?: string;
  },
): Promise<StoryReceipt> {
  return request<StoryReceipt>(`/${workspaceId}/story/changes`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function readStoryChange(
  workspaceId: string,
  changeId: string,
): Promise<StoryChangeRecord> {
  return request<StoryChangeRecord>(
    `/${workspaceId}/story/changes/${encodeURIComponent(changeId)}`,
  );
}

export function undoStoryChange(
  workspaceId: string,
  changeId: string,
  requestKey: string,
): Promise<StoryReceipt> {
  return request<StoryReceipt>(
    `/${workspaceId}/story/changes/${encodeURIComponent(changeId)}/undo?request_key=${encodeURIComponent(requestKey)}`,
    { method: "POST" },
  );
}
