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
  thread_ids: string[];
  clue_ids: string[];
  primary_thread_id?: string | null;
  is_convergence?: boolean;
  reveal_order?: number | null;
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

export interface StoryKnowledgeChange {
  subject_id: string;
  claim: string;
  stance: "knows" | "believes";
  source_kind: "observation" | "inference";
  source_node_ids: string[];
  effective_event_node_id: string;
}

export interface StoryMysteryUse {
  clue_id: string;
  title: string;
  roles: string[];
  presentation: string;
  mode: string;
  speaker_id: string;
  expected_interpretation: string;
  node_id: string;
  node_title: string;
}

export interface StoryMysteryClue {
  clue_id: string;
  title: string;
  roles: string[];
  author_truth: string | null;
  intention: string;
  resolution_intent: "explain" | "leave_open" | "undecided";
  has_identity: boolean;
  uses: StoryMysteryUse[];
}

export interface StoryMysteryNode {
  id: string;
  time_status: "registered" | "unregistered";
  before_ids: string[];
  after_ids: string[];
  clue_uses: StoryMysteryUse[];
  knowledge_changes: StoryKnowledgeChange[];
}

export interface StoryMysteryDiagnostic {
  code: string;
  object_id: string;
  related_ids: string[];
  message: string;
  suggestion: string;
}

export interface StoryMystery {
  variant_id: string;
  revision: string;
  order: string[];
  time_status: {
    state: "complete" | "partial" | "unregistered";
    registered: number;
    unregistered: number;
    total: number;
  };
  nodes: StoryMysteryNode[];
  clues: StoryMysteryClue[];
  subjects: {
    subject_id: string;
    claims: number;
    stances: string[];
    node_ids: string[];
  }[];
  diagnostics: StoryMysteryDiagnostic[];
  counts: {
    nodes: number;
    clues: number;
    event_times: number;
    knowledge_changes: number;
    subjects: number;
  };
}

export interface StorySyncCounts {
  aligned: number;
  outline_ahead: number;
  prose_ahead: number;
  diverged: number;
  unchecked: number;
  waived: number;
  outline_only: number;
  unbound_prose: number;
}

export type StorySyncNodeState =
  | "aligned"
  | "outline_ahead"
  | "prose_ahead"
  | "diverged"
  | "unchecked"
  | "waived"
  | "outline_only";

export type StorySyncChapterState =
  | "aligned"
  | "outline_ahead"
  | "prose_ahead"
  | "diverged"
  | "unchecked"
  | "waived"
  | "unbound";

export interface StorySyncNode {
  node_id: string;
  title: string;
  state: StorySyncNodeState;
  chapter_paths: string[];
  binding_ids: string[];
  contract_digest: string;
  bound_quote: string;
  bound_chapter: string;
}

export interface StorySyncChapter {
  path: string;
  chapter: number;
  title: string;
  state: StorySyncChapterState;
  node_ids: string[];
  prose_digest: string;
  checked_digest: string;
}

export interface StorySyncConflict {
  conflict_id: string;
  kind:
    | "outline_ahead"
    | "prose_ahead"
    | "diverged"
    | "unchecked"
    | "missing_presentation"
    | "unbound_prose";
  node_id: string;
  binding_id?: string;
  path: string;
  message: string;
  options: string[];
}

export interface StorySync {
  variant_id: string;
  revision: string;
  counts: StorySyncCounts;
  nodes: StorySyncNode[];
  chapters: StorySyncChapter[];
  conflicts: StorySyncConflict[];
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
  sync?: {
    conflict_digest: string;
    choice: "follow_outline" | "follow_prose" | "defer";
    question_id?: string;
    note?: string;
  } | null;
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

export function readStoryMystery(
  workspaceId: string,
  variantId: string,
): Promise<StoryMystery> {
  return request<StoryMystery>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/mystery`,
  );
}

export function readStorySync(
  workspaceId: string,
  variantId: string,
  chapterPath?: string,
): Promise<StorySync> {
  const query = chapterPath ? `?chapter_path=${encodeURIComponent(chapterPath)}` : "";
  return request<StorySync>(
    `/${workspaceId}/story/variants/${encodeURIComponent(variantId)}/sync${query}`,
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
    sync?: {
      conflict_digest: string;
      choice: "follow_outline" | "follow_prose" | "defer";
      question_id?: string;
      note?: string;
    };
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
