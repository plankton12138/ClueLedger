/**
 * 写作工作台的接口（1.0.9 P4）。
 *
 * 与后端 `workspace/story_api.py`、`workspace/creative_api.py` 一一对应。页面只发送
 * 自然语言、章节标识与「读到的版本」，标识、修订与路径都由服务端生成；候选读取只按
 * 标识，不在客户端拼路径。
 */

import { request } from "./workspace";

export type AnchorState = "exact" | "relocated" | "missing" | "ambiguous" | "unavailable";
export type Placement = "written" | "observed_candidate" | "alignment";
export type AnalysisState = "not_needed" | "queued" | "running" | "ready" | "unavailable" | "failed" | "stale";

export interface InformationOccurrence {
  path: string;
  digest: string;
  quote: string;
  start: number | null;
  end: number | null;
  state: AnchorState;
}

export interface InformationIndexItem {
  item_id: string;
  thread_id: string | null;
  move_id: string | null;
  label: string;
  placement: Placement;
  occurrences: InformationOccurrence[];
  candidate_id: string | null;
  explanation: string | null;
}

export interface IndexAnalysis {
  state: AnalysisState;
  based_on_digest: string | null;
  operation_id: string | null;
}

export interface ChapterInformationIndex {
  index_revision: string;
  plan_revision: string;
  context_revision: string;
  prose_digest: string | null;
  analysis: IndexAnalysis;
  items: InformationIndexItem[];
}

export interface ChapterMoveView {
  id: string;
  thread_id: string;
  thread_summary: string;
  statement: string;
  effect: string | null;
}

export interface ChapterPlanView {
  id: string;
  order: number;
  title: string | null;
  prose_path: string | null;
  moves: ChapterMoveView[];
}

export interface ChapterSummary {
  id: string;
  order: number;
  title: string | null;
  summary: string;
  prose_path: string | null;
}

export interface InformationThreadSummary {
  id: string;
  summary: string;
}

export interface UnplannedProse {
  path: string;
  title: string;
  /** 不含章序前缀的标题名，用于改名输入。 */
  name: string;
  digest: string;
  chapter_number: number | null;
}

export type ClueStatus = "planted" | "advanced" | "resolved" | "retired";
export type ClueOrigin = "planned" | "emergent" | "migrated";

/** 一条线索在只读面板里的样子（v1.3 P4）。 */
export interface ClueEntryView {
  id: string;
  kind: string;
  status: ClueStatus;
  origin: ClueOrigin;
  note: string | null;
  anchor_prefix: string | null;
  planted_in: number | null;
  resolved_in: number | null;
}

export interface CluePlanView {
  id: string;
  note: string | null;
  plant_chapter: number;
  resolve_chapter: number | null;
}

export interface ClueFindingView {
  code: string;
  message: string;
}

export interface ChapterCluesView {
  chapter: number;
  entries: ClueEntryView[];
  plans: CluePlanView[];
  findings: ClueFindingView[];
}

export interface BookCluesView {
  entries: ClueEntryView[];
  plans: CluePlanView[];
  findings: ClueFindingView[];
}

export interface WritingBookView {
  structure_exists: boolean;
  chapters: ChapterSummary[];
  information_threads: InformationThreadSummary[];
  unplanned_prose: UnplannedProse[];
  /** 全书线索：聚合状态 + 结构层计划 + 对账发现项。 */
  clues: BookCluesView;
}

export interface WritingProseView {
  path: string;
  digest: string;
  text: string;
  editable: false;
}

export interface ChapterView {
  plan: ChapterPlanView | null;
  prose: WritingProseView | null;
  index: ChapterInformationIndex | null;
  /** 本章线索登记与发现项；未挂章节的正文为 null。 */
  clues: ChapterCluesView | null;
}

export interface ModelSupportView {
  available: boolean;
  disabled_reason: string | null;
  supported_assists: string[];
}

export interface WritingWorkbenchView {
  workspace_id: string;
  selection: Record<string, string>;
  plan_revision: string | null;
  book: WritingBookView;
  chapter: ChapterView | null;
  model_support: ModelSupportView;
  view_revision: string;
}

export interface WorkbenchSelection {
  chapterId?: string | null;
  prosePath?: string | null;
}

export interface ProseSaveResult {
  path: string;
  prose_digest: string;
  plan_revision: string;
  context_revision: string;
  index_revision: string;
  index: ChapterInformationIndex;
  analysis_job_id: string | null;
  committed_change_id: string;
  /** 本次保存产生的修订；首次起草、纯追加或内容未变时为空。 */
  revision_id: string | null;
}

export type CandidateStatus = "streaming" | "ready" | "stale" | "failed" | "discarded";
export type AssistKind =
  | "chapter_direction"
  | "title"
  | "prose_draft"
  | "prose_continue"
  | "alignment_resolution"
  | "idea"
  | "module_content"
  | "next_action";

export interface CreativeTarget {
  kind: string;
  workspace_id?: string | null;
  chapter_id?: string | null;
  draft_id?: string | null;
}

export interface CandidateBase {
  plan_revision?: string | null;
  context_revision?: string | null;
  prose_path?: string | null;
  prose_digest?: string | null;
  draft_revision?: string | null;
}

export interface ProposedBinding {
  move_id: string;
  quote: string;
}

/** 计划外线索的登记建议（v1.3 P5）：采用候选保存时由服务端定位段落并落盘。 */
export interface ClueProposalView {
  kind: string;
  note: string;
  quote: string;
}

export interface CreativeCandidate {
  candidate_id: string;
  workspace_id: string | null;
  target: CreativeTarget;
  kind: string;
  text: string;
  alternatives: string[];
  base: CandidateBase;
  proposed_bindings: ProposedBinding[];
  status: CandidateStatus;
  error: string | null;
  request_key: string;
  created_at: string;
  updated_at: string;
  /** 计划外线索登记建议；没有新埋线索时为空。 */
  clue_proposals?: ClueProposalView[];
  /** 解析失败的登记行（原文 + 原因），供作者核对。 */
  clue_proposal_rejects?: string[];
}

export interface AssistInput {
  kind: AssistKind;
  target: CreativeTarget;
  base?: CandidateBase;
  selection?: Record<string, unknown> | null;
  author_note?: string | null;
  draft_text?: string | null;
  request_key: string;
}

/** 工作台的读取：选择章节时带 `chapter_id`，只读打开文件时带 `prose_path`。 */
export function readWorkbench(
  workspaceId: string,
  selection: WorkbenchSelection = {},
  signal?: AbortSignal,
): Promise<WritingWorkbenchView> {
  const params = new URLSearchParams();
  if (selection.chapterId) params.set("chapter_id", selection.chapterId);
  else if (selection.prosePath) params.set("prose_path", selection.prosePath);
  const query = params.size ? `?${params}` : "";
  return request<WritingWorkbenchView>(`/${workspaceId}/story/workbench${query}`, { signal });
}

export function renameChapter(
  workspaceId: string,
  chapterId: string,
  input: { title?: string | null; expected_plan_revision: string; request_key: string },
): Promise<{ chapter_id: string; title: string | null; plan_revision: string; change_id: string }> {
  return request(`/${workspaceId}/story/chapters/${chapterId}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function renameUnplannedProse(
  workspaceId: string,
  input: { path: string; title: string; expected_digest: string },
): Promise<{ path: string; title: string; digest: string }> {
  return request(`/${workspaceId}/story/prose-title`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function saveChapterProse(
  workspaceId: string,
  chapterId: string,
  input: {
    text: string;
    expected_digest: string;
    draft_revision: string;
    candidate_id?: string | null;
    request_key: string;
  },
): Promise<ProseSaveResult> {
  return request(`/${workspaceId}/story/chapters/${chapterId}/prose`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export interface IndexConfirmResult {
  chapter_id: string;
  thread: { id: string; summary: string };
  move: { id: string; thread_id: string; chapter_id: string; statement: string };
  binding: { id: string; move_id: string; path: string; quote: string; start: number; end: number };
  plan_revision: string;
  change_id: string;
}

export function confirmIndexCandidate(
  workspaceId: string,
  candidateId: string,
  input: { chapter_id: string; expected_prose_digest: string; request_key: string },
): Promise<IndexConfirmResult> {
  return request(`/${workspaceId}/story/index-candidates/${encodeURIComponent(candidateId)}/confirm`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function reviewChapterIndex(
  workspaceId: string,
  chapterId: string,
): Promise<{ analysis_job_id: string | null }> {
  return request(`/${workspaceId}/story/chapters/${chapterId}/index-review`, { method: "POST" });
}

export function startAssist(workspaceId: string, input: AssistInput): Promise<CreativeCandidate> {
  return request<CreativeCandidate>(`/${workspaceId}/creative-assists`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function readAssist(workspaceId: string, candidateId: string): Promise<CreativeCandidate> {
  return request<CreativeCandidate>(
    `/${workspaceId}/creative-assists/${encodeURIComponent(candidateId)}`,
  );
}

export function stopAssist(workspaceId: string, candidateId: string): Promise<CreativeCandidate> {
  return request<CreativeCandidate>(
    `/${workspaceId}/creative-assists/${encodeURIComponent(candidateId)}/stop`,
    { method: "POST" },
  );
}

/** 同一次操作重试沿用同一个键；新输入必须重新生成。 */
export function writingKey(prefix: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}
