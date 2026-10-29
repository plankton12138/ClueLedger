/**
 * 构思结构的作品接口（1.0.1 P5）。
 *
 * 与后端 `workspace/story_api.py` 一一对应：读取方案、图与节点详情，提交确定性变更，
 * 建备选、看差异、回看与撤销。这里不做语义推断，也不在客户端拼 ID——所有标识来自服务端。
 */

import { request, requestApi } from "./workspace";

export interface StoryCounts {
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

export interface StoryNode {
  id: string;
  title: string;
  action: string;
  threads: string[];
  clues: string[];
  time_note: string;
  group_id: string | null;
}

export interface StoryEdge {
  id: string;
  from_id: string;
  to_id: string;
  kind: "next" | "reframe" | "answer";
  reason: string;
}

export interface StoryGraph {
  variant_id: string;
  name: string;
  revision: string;
  nodes: StoryNode[];
  edges: StoryEdge[];
  threads: { id: string; title: string }[];
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

export interface StoryClueUse {
  clue_id: string;
  title: string;
  author_truth: string | null;
  intention: string;
  presentation: string;
  mode: string;
  speaker_id: string;
  expected_interpretation: string;
}

export interface StoryNodeDetail {
  variant_id: string;
  revision: string;
  node: StoryNode;
  clue_uses: StoryClueUse[];
  knowledge_changes: Record<string, unknown>[];
  bindings: {
    id: string;
    path: string;
    start: number;
    end: number;
    quote: string;
    observation: string;
  }[];
  incoming: StoryEdge[];
  outgoing: StoryEdge[];
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

export interface StoryAdoptionPlan {
  source: string;
  target: string;
  node_ids: string[];
  copied: Record<string, string[]>;
  unchanged: string[];
  pulled_in: string[];
  skipped_edges: string[];
  conflicts: { kind: string; id: string; reason: string }[];
  operations: number;
  note: string;
}

export interface StoryAdoptionResult {
  plan: StoryAdoptionPlan;
  receipt: StoryReceipt | null;
}

export interface StoryComparison {
  base: string;
  base_revision: string;
  candidate: string;
  candidate_revision: string;
  added: Record<string, string[]>;
  removed: Record<string, string[]>;
  updated: Record<string, string[]>;
  note: string;
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
  groupId?: string,
): Promise<StoryGraph> {
  const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : "";
  return request<StoryGraph>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}${query}`,
  );
}

export function readStoryNode(
  workspaceId: string,
  variantId: string,
  nodeId: string,
): Promise<StoryNodeDetail> {
  return request<StoryNodeDetail>(
    `/${workspaceId}/story/nodes/${encodeURIComponent(nodeId)}?variant_id=${encodeURIComponent(variantId)}`,
  );
}

/** 界面发起的确定性结构变更：修订不符返回 409，同键同负载只应用一次。 */
export function submitStoryChange(
  workspaceId: string,
  body: {
    variant_id: string;
    expected_revision: string;
    request_key: string;
    operations: Record<string, unknown>[];
    note?: string;
  },
): Promise<StoryReceipt> {
  return request<StoryReceipt>(`/${workspaceId}/story/changes`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createStoryVariant(
  workspaceId: string,
  body: {
    name: string;
    source_variant_id: string;
    source_revision: string;
    request_key: string;
  },
): Promise<StoryReceipt> {
  return request<StoryReceipt>(`/${workspaceId}/story/variants`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function compareStoryVariants(
  workspaceId: string,
  base: string,
  candidate: string,
): Promise<StoryComparison> {
  const query = `?base=${encodeURIComponent(base)}&candidate=${encodeURIComponent(candidate)}`;
  return request<StoryComparison>(`/${workspaceId}/story/comparison${query}`);
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

/** 采用备选：整案或选定片段；只复制结构，冲突由服务端逐项返回。 */
export function adoptStoryVariant(
  workspaceId: string,
  input: {
    source_variant_id: string;
    source_revision: string;
    target_variant_id: string;
    target_revision: string;
    node_ids?: string[];
    mode?: "exact" | "assist";
    replace?: boolean;
    request_key: string;
  },
): Promise<StoryAdoptionResult> {
  return request<StoryAdoptionResult>(`/${workspaceId}/story/adoptions`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 操作上下文里的结构注入：查看「这一轮实际用了哪些结构」。 */
export function readOperationContext(operationId: string): Promise<{
  operation_id: string;
  model: string;
  connection: string;
  refs: Record<string, unknown>[];
  injections: Record<string, unknown>[];
  note: string;
}> {
  return requestApi(`/api/operations/${encodeURIComponent(operationId)}/context`);
}
