/**
 * 修订学习接口（v1.2）。
 *
 * 修订由服务端从已入稿的正文改动派生；差异片段与「现在还能不能分析」都由服务端判定，
 * 页面只负责展示与发起分析。写作约定是作品根目录的 `STYLE.md`，整份读写。
 */

import { request } from "./workspace";

export type RevisionSource = "author" | "model" | "candidate" | "author_after_model";
export type DecisionTarget = "style" | "structure" | "module_entry" | "chapter_only";
export type AnalysisStatus = "queued" | "running" | "ready" | "failed";

export interface RevisionSummary {
  id: string;
  chapter_id: string;
  path: string;
  change_id: string;
  source: RevisionSource;
  before_digest: string;
  after_digest: string;
  confirmed_at: string;
  author_message: string;
  operation_id: string;
  candidate_ref: string;
  conversation_id: string;
  hunk_count: number;
  added_chars: number;
  removed_chars: number;
  active_hunk_count: number;
  analyzable: boolean;
}

export interface InlineSpan {
  kind: "equal" | "insert" | "delete";
  text: string;
}

export interface RevisionHunk {
  id: string;
  op: "replace" | "insert" | "delete";
  before: string;
  after: string;
  inline: InlineSpan[];
  before_index: number;
  before_count: number;
  after_index: number;
  after_count: number;
  active: boolean;
}

export interface RevisionDetail {
  revision: RevisionSummary;
  hunks: RevisionHunk[];
  active_hunk_ids: string[];
  analyzable: boolean;
  current_text_available: boolean;
}

export interface AnalysisDecision {
  hunk_ids: string[];
  target: DecisionTarget;
  reason: string;
  proposal: string;
  evidence: string;
  target_label: string;
  module_id: string;
  revision_id: string;
  entry_id: string;
  entry_digest: string;
  patch_find: string;
  patch_replace: string;
  patch_error: string;
  applied: boolean;
}

export interface AnalysisTraceStep {
  label: string;
  status: "done" | "running" | "failed";
  detail: string;
  at: string;
}

export interface AnalysisTraceRead {
  label: string;
  detail: string;
}

export interface AnalysisTrace {
  model: string;
  connection: string;
  started_at: string;
  finished_at?: string;
  reads: AnalysisTraceRead[];
  steps: AnalysisTraceStep[];
}

export interface RevisionAnalysis {
  id: string;
  revision_id: string;
  chapter_id: string;
  conversation_id: string;
  operation_id: string;
  status: AnalysisStatus;
  created_at: string;
  updated_at: string;
  summary: string;
  decisions: AnalysisDecision[];
  style_digest: string;
  structure_digest: string;
  assembly_digest: string;
  proposed_style_text: string;
  error: string;
  trace: AnalysisTrace;
}

export interface StyleDocument {
  path: string;
  exists: boolean;
  text: string;
  digest: string | null;
  size: number;
}

export function listRevisions(
  workspaceId: string,
  chapterId: string,
): Promise<{ chapter_id: string; path: string; items: RevisionSummary[] }> {
  return request(`/${workspaceId}/story/chapters/${chapterId}/revisions`);
}

export function readRevision(workspaceId: string, revisionId: string): Promise<RevisionDetail> {
  return request(`/${workspaceId}/story/revisions/${revisionId}`);
}

export function startRevisionAnalysis(
  workspaceId: string,
  revisionId: string,
  input: {
    hunk_ids: string[];
    conversation_id?: string;
    operation_id?: string;
    request_key: string;
  },
): Promise<RevisionAnalysis> {
  return request(`/${workspaceId}/story/revisions/${revisionId}/analyses`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function readRevisionAnalysis(
  workspaceId: string,
  analysisId: string,
): Promise<RevisionAnalysis> {
  return request(`/${workspaceId}/story/revision-analyses/${analysisId}`);
}

export function applyRevisionAnalysis(
  workspaceId: string,
  analysisId: string,
  input: {
    text: string;
    expected_digest: string | null;
    decision_indexes: number[];
    request_key: string;
  },
): Promise<{ analysis_id: string; style: { path: string; digest: string; text: string } | null }> {
  return request(`/${workspaceId}/story/revision-analyses/${analysisId}/applications`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function applyStructureDecisions(
  workspaceId: string,
  analysisId: string,
  input: { decision_indexes: number[]; expected_digest: string | null },
): Promise<{
  analysis: RevisionAnalysis;
  structure: { path: string; digest: string; text: string };
  decision_indexes: number[];
}> {
  return request(`/${workspaceId}/story/revision-analyses/${analysisId}/structure-applications`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function applyModuleDecisions(
  workspaceId: string,
  analysisId: string,
  input: { decision_indexes: number[]; expected_digest: string | null },
): Promise<{
  analysis: RevisionAnalysis;
  change_set_id: string;
  assembly: Record<string, unknown>;
  decision_indexes: number[];
}> {
  return request(`/${workspaceId}/story/revision-analyses/${analysisId}/module-applications`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function readStyle(workspaceId: string): Promise<StyleDocument> {
  return request(`/${workspaceId}/story/style`);
}

export function saveStyle(
  workspaceId: string,
  input: { text: string; expected_digest: string | null; request_key: string },
): Promise<{ path: string; text: string; digest: string; replayed: boolean }> {
  return request(`/${workspaceId}/story/style`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}
