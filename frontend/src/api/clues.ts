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
}
export interface ClueDetail extends ClueSummary {
  workspace_lineage: string;
  ledger_revision: string;
  observations: { observation_id: string; chapter: number; kind: "plant" | "mention" | "resolve"; mode: ClueMode; text: string; evidence_ids: string[] }[];
  reader_seen: { text: string; mode: ClueMode; basis_ids: string[] }[];
  evidence: { evidence_id: string; path: string; digest: string; current_digest: string | null; chapter: number; start: number; end: number; quote: string; freshness: ClueFreshness }[];
  interpretation: string;
  observations_truncated: boolean;
  author_designs: ClueDesign[];
  inferences: ClueInference[];
  reviews: ClueReviewSummary[];
}
export type ClueIntent = "deduce" | "suspect" | "keep_open" | "undecided";
export type DesignFreshness = "valid" | "stale" | "detached" | "source_missing" | "unavailable" | "invalid_range";
export interface ClueDesign {
  link_id: string;
  relation: string;
  source: SourceRef;
  freshness: DesignFreshness;
  quote: string;
  evidence_id: string;
  label: string;
}
export interface InferenceStatement {
  text: string;
  clue_ids: string[];
  design_link_ids: string[];
}
export interface ClueInference {
  inference_id: string;
  inference_revision: string;
  goal: string;
  intent: ClueIntent;
  clue_ids: string[];
  clues: { clue_id: string; title: string }[];
  premises: InferenceStatement[];
  alternatives: InferenceStatement[];
  design_link_ids: string[];
  updated_at: string;
  warnings: string[];
  suggested_chapters: string[];
}
export type ClueReviewFreshness = "valid" | "stale" | "source_missing" | "target_changed";
export type ClueReviewStatus = "findings" | "no_findings" | "incomplete";
export type ClueFindingKind = "inference_gap" | "alternative_open" | "premise_undisclosed" | "counter_evidence";
export interface ClueReviewSummary {
  report_id: string;
  inference_id: string;
  status: ClueReviewStatus;
  freshness: ClueReviewFreshness;
  created_at: string;
}
export interface ClueReviewFinding {
  kind: ClueFindingKind;
  certainty: "supported" | "suspected";
  description: string;
  suggestion: string;
  clue_ids: string[];
  evidence: { path: string; digest: string; start: number; end: number; quote: string; evidence_id?: string }[];
}
export interface ClueReviewReport {
  report_id: string;
  inference_id: string;
  goal: string;
  intent: ClueIntent;
  clue_ids: string[];
  as_of_chapter: number;
  status: ClueReviewStatus;
  findings: ClueReviewFinding[];
  uncovered: string[];
  coverage: { path: string; complete: boolean; read_chars: number; total_chars: number; note: string }[];
  target_paths: string[];
  created_at: string;
  freshness: ClueReviewFreshness;
  reasons: string[];
  later_chapters: number;
  truncated: boolean;
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

async function clueWrite<T>(path: string, method: "POST" | "PUT", body: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = payload?.error;
    throw new WorkspaceApiError(error?.code ?? `HTTP_${response.status}`, error?.why ?? "线索操作没有完成", error?.fix ?? "重新读取线索后重试", response.status, error?.where);
  }
  return response.json() as Promise<T>;
}

export function clueRequestKey(prefix: string): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

function asOfQuery(asOf: number | null): string {
  return asOf === null ? "" : `?as_of_chapter=${asOf}`;
}

export function createClue(
  workspaceId: string,
  input: { title: string; requestKey: string; expectedRevision: string; asOf: number | null },
): Promise<ClueDetail> {
  return clueWrite(
    `/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues${asOfQuery(input.asOf)}`,
    "POST",
    { title: input.title, request_key: input.requestKey, expected_revision: input.expectedRevision },
  );
}

export function linkClueDesign(
  workspaceId: string,
  clueId: string,
  input: {
    requestKey: string;
    expectedRevision: string;
    source: SourceRef;
    start: number;
    end: number;
    quote: string;
    relation: string;
    asOf: number | null;
  },
): Promise<ClueDetail> {
  return clueWrite(
    `/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues/${encodeURIComponent(clueId)}/design${asOfQuery(input.asOf)}`,
    "PUT",
    {
      request_key: input.requestKey,
      expected_revision: input.expectedRevision,
      source: input.source,
      start: input.start,
      end: input.end,
      quote: input.quote,
      relation: input.relation,
    },
  );
}

export function unlinkClueDesign(
  workspaceId: string,
  clueId: string,
  input: { requestKey: string; expectedRevision: string; linkId: string; asOf: number | null },
): Promise<ClueDetail> {
  return clueWrite(
    `/api/file-workspaces/${encodeURIComponent(workspaceId)}/clues/${encodeURIComponent(clueId)}/design/unlink${asOfQuery(input.asOf)}`,
    "POST",
    { request_key: input.requestKey, expected_revision: input.expectedRevision, link_id: input.linkId },
  );
}

export function saveClueInference(
  workspaceId: string,
  input: {
    requestKey: string;
    expectedRevision: string;
    inferenceId?: string | null;
    inferenceRevision?: string | null;
    clueIds: string[];
    goal: string;
    intent: ClueIntent;
    premises: InferenceStatement[];
    alternatives: InferenceStatement[];
    designLinkIds: string[];
  },
): Promise<ClueInference> {
  return clueWrite(
    `/api/file-workspaces/${encodeURIComponent(workspaceId)}/clue-inferences`,
    "POST",
    {
      request_key: input.requestKey,
      expected_revision: input.expectedRevision,
      inference_id: input.inferenceId ?? null,
      inference_revision: input.inferenceRevision ?? null,
      clue_ids: input.clueIds,
      goal: input.goal,
      intent: input.intent,
      premises: input.premises,
      alternatives: input.alternatives,
      design_link_ids: input.designLinkIds,
    },
  );
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
  inference_id?: string;
  inference_revision?: string;
  report_id?: string;
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

export function fetchClueReview(workspaceId: string, reportId: string, asOf: number | null): Promise<ClueReviewReport> {
  const params = asOf === null ? "" : `?as_of_chapter=${asOf}`;
  return clueRequest(
    `/api/file-workspaces/${encodeURIComponent(workspaceId)}/clue-reviews/${encodeURIComponent(reportId)}${params}`,
  );
}
