/**
 * 构思结构的作品接口（1.0.1 P5）。
 *
 * 与后端 `workspace/story_api.py` 一一对应：读取方案、图与节点详情，提交确定性变更，
 * 回看与撤销。这里不做语义推断，也不在客户端拼 ID——所有标识来自服务端。
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

export interface StoryNode {
  id: string;
  title: string;
  action: string;
  change: string | null;
  stage_id: string | null;
  threads: string[];
  clues: string[];
  time_note: string;
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
