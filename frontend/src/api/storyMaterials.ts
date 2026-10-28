/**
 * 故事资料作者视图与整理任务的客户端（0.7）。
 *
 * 对应后端 `/api/file-workspaces/{id}/story-materials` 与 `/material-tasks`。
 * 条目按域返回 `data`，这里用判别联合收窄：界面拿到的字段与后端契约一一对应，
 * 不用任意 JSON 当契约。
 */

import { WorkspaceApiError } from "./workspace";

export type MaterialDomain =
  | "story_index"
  | "chapter_index"
  | "disclosure"
  | "roster"
  | "timeline"
  | "clues"
  | "glossary";

export type Freshness = "valid" | "stale" | "source_missing" | "unavailable" | "none";
export type CoverageOutcome = "found" | "empty" | "insufficient" | "unrecorded";
export type MaterialOutcome = "found" | "empty" | "insufficient";

export interface SourceEvidence {
  evidence_id?: string | null;
  path: string;
  digest: string;
  chapter: number | null;
  start: number;
  end: number;
  quote: string;
  freshness: Freshness;
}

interface EntryBase {
  id: string;
  title: string;
  subtitle: string;
  chapter: number | null;
  entity_path: string | null;
  coverage: CoverageOutcome;
  freshness: Freshness;
  task_id: string;
  updated_at: string;
  evidence: SourceEvidence[];
  notes: string[];
}

export interface ChapterIndexData {
  summary: string;
  events: string[];
  characters: string[];
  places: string[];
  clues: string[];
}

export interface TimelineData {
  when: string;
  span: string;
  location: string;
}

export interface ClueData {
  clue_id: string;
  clue: string;
  planted_chapter: number | null;
  due_chapter: number | null;
  status: "open" | "resolved" | "dropped" | "unknown";
  label: string;
  overdue: boolean;
  supports: string;
  resolution_chapter: number | null;
}

export interface GlossaryData {
  term: string;
  kind: "person" | "place" | "org" | "item";
  label: string;
  aliases: string[];
  first_chapter: number;
  source: "declared" | "text";
}

export interface DisclosureFact {
  fact: string;
  chapter: number;
  source?: "text" | "declared";
}

export interface DisclosureData {
  name: string;
  surface: string;
  aliases: string[];
  roles: string[];
  revealed: DisclosureFact[];
  upcoming: DisclosureFact[];
  planned: DisclosureFact[];
  verified: number;
}

export interface RosterData {
  name: string;
  location: string;
  /** null 表示没有核对记录：生死未知，不能当成「还活着」。 */
  alive: boolean | null;
  stance: string;
  chapter: number | null;
  lag: number | null;
}

export type MaterialEntry =
  | (EntryBase & { domain: "chapter_index"; data: ChapterIndexData })
  | (EntryBase & { domain: "timeline"; data: TimelineData })
  | (EntryBase & { domain: "clues"; data: ClueData })
  | (EntryBase & { domain: "glossary"; data: GlossaryData })
  | (EntryBase & { domain: "disclosure"; data: DisclosureData })
  | (EntryBase & { domain: "roster"; data: RosterData });

export interface MaterialChapter {
  path: string;
  chapter: number;
  title: string;
  digest: string;
}

export interface MaterialEntity {
  path: string;
  name: string;
}

export interface MaterialCoverage {
  total: number;
  found: number;
  empty: number;
  insufficient: number;
  unrecorded: number;
  stale: number;
}

export interface MaterialConflict {
  term?: string;
  path?: string;
  name?: string;
  kind: string;
  label?: string;
  detail: string;
}

export interface StoryMaterials {
  domain: MaterialDomain;
  label: string;
  as_of_chapter: number;
  current_chapter: number;
  chapters: MaterialChapter[];
  entities: MaterialEntity[];
  entries: MaterialEntry[];
  total: number;
  next_cursor: string;
  coverage: MaterialCoverage;
  warnings: string[];
  conflicts: MaterialConflict[];
}

export interface MaterialScope {
  domain: MaterialDomain;
  chapter_paths: string[];
  entity_paths: string[];
  as_of_chapter: number;
  observed_digests: Record<string, string>;
}

export interface MaterialTaskAccepted {
  turn_id: string;
  conversation_id: string;
  origin: "materials";
  domain: MaterialDomain;
  label: string;
  title: string;
  model: string;
  request_key: string;
  scope: {
    domain: MaterialDomain;
    chapter_paths: string[];
    entity_paths: string[];
    as_of_chapter: number;
  };
}

export interface MaterialTarget {
  path: string;
  chapter: number;
  entity_path: string;
  outcome: CoverageOutcome;
  freshness: Freshness;
}

export type MaterialTaskStatus =
  | "accepted"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "stopped"
  | "interrupted";

export interface MaterialTask {
  turn_id: string;
  conversation_id: string;
  domain: MaterialDomain;
  label: string;
  scope: {
    domain: MaterialDomain;
    chapter_paths: string[];
    entity_paths: string[];
    as_of_chapter: number;
  };
  status: MaterialTaskStatus;
  model: string;
  started_at: string;
  published: number;
  done: number;
  total: number;
  targets: MaterialTarget[];
  unfinished: MaterialTarget[];
}

export type StoryOrder = "chapter" | "story";

export interface StoryTime {
  label: string;
  normalized: string;
  before: string[];
  after: string[];
}

export interface StoryEvent {
  id: string;
  text: string;
  chapter: number;
  mode: string;
  mode_label: string;
  speaker: string[] | null;
  related: string[][];
  author_event: string[] | null;
  story_time: StoryTime;
  evidence: { path: string; start: number; end: number; quote: string }[];
}

export interface StoryChapterRow {
  path: string;
  chapter: number;
  title: string;
  source: "story_index" | "legacy" | "none";
  status: string;
  summary: string;
  events: StoryEvent[];
  legacy_entries: {
    events: string[];
    characters: string[];
    places: string[];
    clues: string[];
  } | null;
  anchor: { when: string; span: string; location: string; source: string } | null;
  coverage: {
    domain: string;
    outcome: CoverageOutcome;
    at: string;
    task_id: string;
    fresh: boolean;
  } | null;
}

export interface StoryGroup {
  provisional: boolean;
  events: StoryEvent[];
}

export interface StoryConflict {
  kind: string;
  detail: string;
  ids: string[];
  events: { id: string; text: string; chapter: number; mode: string; mode_label: string }[];
}

export interface StoryPlan {
  binding_id: string;
  module_id: string;
  revision_id: string;
  module_kind: string;
  module_name: string;
  entry_id: string;
  title: string;
  story_time: string;
  planned_chapter: number | null;
  decision: string;
  excerpt: string;
}

export interface StoryPlanModule {
  binding_id: string;
  module_id: string;
  revision_id: string;
  kind: string;
  name: string;
}

export interface StoryEvents {
  mode: StoryOrder;
  as_of_chapter: number;
  chapters: StoryChapterRow[];
  groups: StoryGroup[];
  unknown: StoryEvent[];
  conflicts: StoryConflict[];
  warnings: string[];
  plans: StoryPlan[];
  plan_modules: StoryPlanModule[];
}

const BASE = "/api/file-workspaces";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; why?: string; fix?: string };
    } | null;
    const error = body?.error;
    throw new WorkspaceApiError(
      error?.code ?? `HTTP_${response.status}`,
      error?.why ?? `请求没有成功（HTTP ${response.status}）`,
      error?.fix ?? "稍后重试；若持续失败，按状态码查后端日志",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchMaterials(
  workspaceId: string,
  domain: MaterialDomain,
  options: {
    asOfChapter?: number | null;
    query?: string;
    limit?: number;
    cursor?: string;
    signal?: AbortSignal;
  } = {},
): Promise<StoryMaterials> {
  const params = new URLSearchParams();
  if (options.asOfChapter !== undefined && options.asOfChapter !== null) {
    params.set("as_of_chapter", String(options.asOfChapter));
  }
  if (options.query) params.set("q", options.query);
  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);
  const suffix = params.toString();
  return request<StoryMaterials>(
    `/${workspaceId}/story-materials/${domain}${suffix ? `?${suffix}` : ""}`,
    { signal: options.signal },
  );
}

export function fetchMaterialEntry(
  workspaceId: string,
  domain: MaterialDomain,
  entryId: string,
  asOfChapter?: number | null,
): Promise<{ entry: MaterialEntry; as_of_chapter: number }> {
  const params = new URLSearchParams();
  if (asOfChapter !== undefined && asOfChapter !== null) {
    params.set("as_of_chapter", String(asOfChapter));
  }
  const suffix = params.toString();
  return request(
    `/${workspaceId}/story-materials/${domain}/entries/${entryId}${suffix ? `?${suffix}` : ""}`,
  );
}

export function fetchStoryEvents(
  workspaceId: string,
  order: StoryOrder,
  options: { asOfChapter?: number | null; signal?: AbortSignal } = {},
): Promise<StoryEvents> {
  const params = new URLSearchParams({ order });
  if (options.asOfChapter !== undefined && options.asOfChapter !== null) {
    params.set("as_of_chapter", String(options.asOfChapter));
  }
  return request<StoryEvents>(`/${workspaceId}/story-events?${params.toString()}`, {
    signal: options.signal,
  });
}

export function startMaterialTask(
  workspaceId: string,
  input: { requestKey: string; scope: MaterialScope },
): Promise<MaterialTaskAccepted> {
  return request<MaterialTaskAccepted>(`/${workspaceId}/material-tasks`, {
    method: "POST",
    body: JSON.stringify({ request_key: input.requestKey, scope: input.scope }),
  });
}

export function listMaterialTasks(
  workspaceId: string,
  domain?: MaterialDomain,
  signal?: AbortSignal,
): Promise<{ items: MaterialTask[] }> {
  const params = new URLSearchParams();
  if (domain) params.set("domain", domain);
  const suffix = params.toString();
  return request<{ items: MaterialTask[] }>(
    `/${workspaceId}/material-tasks${suffix ? `?${suffix}` : ""}`,
    { signal },
  );
}

export function fetchMaterialTask(
  workspaceId: string,
  turnId: string,
  signal?: AbortSignal,
): Promise<MaterialTask> {
  return request<MaterialTask>(`/${workspaceId}/material-tasks/${turnId}`, { signal });
}

export function stopMaterialTask(
  workspaceId: string,
  turnId: string,
): Promise<{ stopped: boolean }> {
  return request<{ stopped: boolean }>(`/${workspaceId}/turns/${turnId}/stop`, {
    method: "POST",
  });
}
