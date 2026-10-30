import { WorkspaceApiError } from "./workspace";

export type ClueFreshness = "valid" | "stale" | "source_missing" | "unavailable" | "none";
export type ClueMode = "narration" | "testimony" | "recollection" | "hypothesis" | "dream" | "unknown";
export interface ClueCoverage {
  outcome: "found" | "empty" | "insufficient" | "unrecorded";
  freshness: ClueFreshness;
  chapter_paths: string[];
  task_id: string;
  at: string;
  note: string;
}
export interface CluePlanView {
  author_truth: string | null;
  intention: string;
  resolution_intent: "explain" | "leave_open" | "undecided";
}
export interface ClueUsePosition {
  node_id: string;
  stage_id: string | null;
  thread_ids: string[];
  roles: string[];
  presentation: string;
  mode: ClueMode;
  speaker_id: string;
  expected_interpretation: string;
}
export interface ClueSummary {
  clue_id: string;
  clue_revision: string;
  title: string;
  as_of_chapter: number;
  lifecycle: { status: "open" | "resolved" | "dropped" | "unknown"; planted_chapter: number | null; due_chapter: number | null; resolution_chapter: number | null };
  freshness: ClueFreshness;
  coverage: ClueCoverage;
  mentioned_chapters: number[];
  warnings: string[];
  scope: "chapter" | "story";
  plan: CluePlanView | null;
}
export interface ClueDetail extends ClueSummary {
  workspace_lineage: string;
  ledger_revision: string;
  story_revision: string;
  view_revision: string;
  uses: ClueUsePosition[];
  observations: { observation_id: string; chapter: number; kind: "plant" | "mention" | "resolve"; mode: ClueMode; text: string; evidence_ids: string[] }[];
  reader_seen: { text: string; mode: ClueMode; basis_ids: string[] }[];
  evidence: { evidence_id: string; path: string; digest: string; current_digest: string | null; chapter: number; start: number; end: number; quote: string; freshness: ClueFreshness }[];
  interpretation: string;
  observations_truncated: boolean;
}
export interface ClueChapter {
  path: string;
  chapter: number;
  title: string;
  digest: string | null;
  has_content: boolean;
  coverage: ClueCoverage;
}
export interface ClueList {
  items: ClueSummary[];
  total: number;
  next_cursor: string;
  as_of_chapter: number;
  current_chapter: number;
  ledger_revision: string;
  view_revision: string;
  chapters: ClueChapter[];
  chapter: ClueChapter | null;
}

async function clueRequest<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error;
    throw new WorkspaceApiError(error?.code ?? `HTTP_${response.status}`, error?.why ?? "线索无法读取", error?.fix ?? "重新读取线索后重试", response.status);
  }
  return response.json() as Promise<T>;
}
export function fetchClues(workspaceId: string, chapterPath: string | null, asOf: number | null, cursor = "", limit = 20): Promise<ClueList> {
  const params = new URLSearchParams();
  if (chapterPath) params.set("chapter_path", chapterPath);
  if (asOf !== null) params.set("as_of_chapter", String(asOf));
  if (cursor) params.set("cursor", cursor);
  if (limit !== 20) params.set("limit", String(limit));
  return clueRequest(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues?${params}`);
}
export function fetchClueDetail(workspaceId: string, clueId: string, asOf: number | null): Promise<ClueDetail> {
  return clueRequest(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues/${encodeURIComponent(clueId)}${asOf === null ? "" : `?as_of_chapter=${asOf}`}`);
}
export function fetchStoryClues(workspaceId: string, variantId: string, nodeId: string | null, asOf: number | null, cursor = "", limit = 20): Promise<ClueList> {
  const params = new URLSearchParams({ scope: "story", variant_id: variantId });
  if (nodeId) params.set("node_id", nodeId);
  if (asOf !== null) params.set("as_of_chapter", String(asOf));
  if (cursor) params.set("cursor", cursor);
  if (limit !== 20) params.set("limit", String(limit));
  return clueRequest(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues?${params}`);
}
export function fetchStoryClueDetail(workspaceId: string, variantId: string, clueId: string, asOf: number | null): Promise<ClueDetail> {
  const params = new URLSearchParams({ scope: "story", variant_id: variantId });
  if (asOf !== null) params.set("as_of_chapter", String(asOf));
  return clueRequest(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues/${encodeURIComponent(clueId)}?${params}`);
}

export type SourceRef =
  | { kind: "file"; path: string; digest: string }
  | { kind: "module"; library_id: string; module_id: string; revision_id: string; entry_id: string; content_digest: string };

export interface SourceLocation {
  evidence_id: string;
  clue_id: string;
  source: SourceRef;
  freshness: "valid" | "stale" | "source_missing" | "unavailable" | "detached" | "invalid_range";
  quote: string;
  chapter: number | null;
  as_of_chapter: number;
  offset_unit: "unicode_codepoint";
  start: number | null;
  end: number | null;
  readable: boolean;
  current_digest: string | null;
  excerpt: string;
  excerpt_start: number;
  saved_path?: string | null;
}

/** S6 将此结构接入受理与草稿；当前定位操作不发起模型请求。 */
export interface ClueReference {
  workspace_lineage: string;
  clue_id: string;
  clue_revision: string;
  browse_as_of_chapter: number;
}

export async function fetchSourceLocation(workspaceId: string, evidenceId: string, asOf: number | null): Promise<SourceLocation> {
  const params = asOf === null ? "" : `?as_of_chapter=${asOf}`;
  const response = await fetch(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/source-locations/${encodeURIComponent(evidenceId)}${params}`);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error;
    throw new WorkspaceApiError(error?.code ?? `HTTP_${response.status}`, error?.why ?? "来源无法读取", error?.fix ?? "刷新线索详情后重试", response.status);
  }
  return response.json() as Promise<SourceLocation>;
}
