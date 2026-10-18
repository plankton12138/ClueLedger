/**
 * V2 REST 客户端（/api/v2）：项目、正文、变更中心、审计、作者模型、Agent 任务。
 *
 * 错误信封统一为 V2 四要素 {"error": {code, category, where, why, fix}}——
 * 错误消息是喂给人的 API：why + fix 原样透出，不改写不吞码。
 * 本模块面向 /api/v2 全量端点。
 */

export interface V2ErrorBody {
  code: string;
  category: string;
  where: string;
  why: string;
  fix: string;
  details: Record<string, unknown>;
}

export class V2Error extends Error {
  readonly code: string;
  readonly category: string;
  readonly where: string;
  readonly fix: string;
  readonly details: Record<string, unknown>;

  constructor(body: V2ErrorBody, status: number) {
    super(
      body.why !== ""
        ? body.fix !== ""
          ? `${body.why}（怎样算对：${body.fix}）`
          : body.why
        : `HTTP ${status}`,
    );
    this.name = "V2Error";
    this.code = body.code;
    this.category = body.category;
    this.where = body.where;
    this.fix = body.fix;
    this.details = body.details;
  }
}

/** 抽错误文案：V2 信封优先，回落 V1 detail，再回落状态码。 */
async function raiseForStatus(res: Response): Promise<void> {
  if (res.ok) return;
  const body: unknown = await res.json().catch(() => null);
  if (body !== null && typeof body === "object") {
    const error = (body as { error?: unknown }).error;
    if (
      error !== null &&
      typeof error === "object" &&
      typeof (error as { why?: unknown }).why === "string"
    ) {
      throw new V2Error(error as V2ErrorBody, res.status);
    }
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail !== "") throw new Error(detail);
  }
  throw new Error(`HTTP ${res.status}`);
}

/** 统一请求：GET（无 body）/ POST（默认）/ 显式 method。对话客户端复用（Phase 19.2）。 */
export async function request<T>(
  url: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const res = await fetch(
    url,
    body === undefined
      ? method === undefined
        ? {}
        : { method }
      : {
          method: method ?? "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  await raiseForStatus(res);
  return (await res.json()) as T;
}

const key = () => crypto.randomUUID();
export const projectPrefix = (projectId: string) =>
  `/api/v2/projects/${encodeURIComponent(projectId)}`;

// ---------------------------------------------------------------------------
// 项目与正文
// ---------------------------------------------------------------------------

export interface ChapterHeadWire {
  chapter_id: string;
  title: string;
  order_key: string;
  chapter_base: string;
  units: { unit_id: string; text: string }[];
}

export interface ManuscriptWire {
  project_id: string;
  commit_id: string | null;
  chapters: ChapterHeadWire[];
}

export interface ChapterWire extends ChapterHeadWire {
  project_id: string;
  commit_id: string | null;
  text: string;
}

export interface CommitResult {
  status: string;
  commit_id: string | null;
  new_head: string | null;
  summary: string;
  warnings?: string[];
  chapter_base?: string;
}

export async function createProject(
  name: string,
): Promise<{ project_id: string; name: string }> {
  return request("/api/v2/projects", { name });
}

export async function fetchManuscript(
  projectId: string,
): Promise<ManuscriptWire> {
  return request(`${projectPrefix(projectId)}/manuscript`);
}

export async function fetchV2Chapter(
  projectId: string,
  chapterId: string,
): Promise<ChapterWire> {
  return request(
    `${projectPrefix(projectId)}/chapters/${encodeURIComponent(chapterId)}`,
  );
}

/** 人工保存正文（P5：立即生效；后台分析失败不阻断保存）。 */
export async function saveV2Chapter(
  projectId: string,
  chapterId: string,
  fullText: string,
  expectedChapterBase: string,
  summary = "人工保存",
): Promise<CommitResult> {
  return request(
    `${projectPrefix(projectId)}/chapters/${encodeURIComponent(chapterId)}/save`,
    {
      idempotency_key: key(),
      full_text: fullText,
      expected_chapter_base: expectedChapterBase,
      summary,
    },
  );
}

export async function restoreV2Chapter(
  projectId: string,
  chapterId: string,
  sourceCommitId: string,
  expectedChapterBase: string,
): Promise<CommitResult> {
  return request(
    `${projectPrefix(projectId)}/chapters/${encodeURIComponent(chapterId)}/restore`,
    {
      idempotency_key: key(),
      source_commit_id: sourceCommitId,
      expected_chapter_base: expectedChapterBase,
    },
  );
}

export async function importChapter(
  projectId: string,
  title: string,
  text: string,
  orderKey: string,
): Promise<CommitResult & { chapter_id: string }> {
  return request(`${projectPrefix(projectId)}/chapters/import`, {
    idempotency_key: key(),
    title,
    text,
    order_key: orderKey,
  });
}

export function exportUrl(projectId: string): string {
  return `${projectPrefix(projectId)}/export`;
}

// ---------------------------------------------------------------------------
// 故事事实（Canon / Narrative Plan 快照）
// ---------------------------------------------------------------------------

export interface StoryArtifact {
  artifact_id: string;
  kind: string;
  content: Record<string, unknown>;
}

export interface StorySnapshot {
  commit_id: string | null;
  artifacts: StoryArtifact[];
}

export async function fetchStorySnapshot(
  projectId: string,
): Promise<StorySnapshot> {
  return request(`${projectPrefix(projectId)}/story`);
}

// ---------------------------------------------------------------------------
// 变更中心
// ---------------------------------------------------------------------------

export interface ChangeSetWire {
  seed_changes?: {
    artifact_id: string;
    proposed_content?: Record<string, unknown> | null;
  }[];
  change_set_id: string;
  intent: string;
  status: string;
  base_commit_id: string;
}

export interface ChangeListWire {
  head_commit_id: string | null;
  changes: ChangeSetWire[];
}

export interface ChangeObject {
  kind: string;
  content: Record<string, unknown>;
}

export interface ChangeDetailWire {
  head_commit_id: string;
  change_set: ChangeSetWire;
  objects: ChangeObject[];
}

export interface ImpactPathNode {
  source: string;
  relation_type: string;
  target: string;
}

export interface ImpactItem {
  target_artifact_id: string;
  severity: string;
  reason: string;
  status: string;
  path: ImpactPathNode[];
}

export interface RevisionStep {
  step_id: string;
  title: string;
  target_artifact_ids: string[];
}

export interface PatchHunk {
  hunk_id: string;
  artifact_id: string;
  field: string;
  old: unknown;
  new: unknown;
}

export async function listChanges(projectId: string): Promise<ChangeListWire> {
  return request(`${projectPrefix(projectId)}/changes`);
}

export async function openChangeSet(
  projectId: string,
  intent: string,
  baseCommitId: string,
  seedIds: string[],
  scope: Record<string, unknown> = {},
  seedPatches: Record<string, Record<string, unknown>> = {},
): Promise<ChangeSetWire> {
  return request(`${projectPrefix(projectId)}/changes`, {
    idempotency_key: key(),
    intent,
    base_commit_id: baseCommitId,
    seed_ids: seedIds,
    scope,
    seed_patches: seedPatches,
  });
}

export async function fetchChangeDetail(
  projectId: string,
  csId: string,
): Promise<ChangeDetailWire> {
  return request(
    `${projectPrefix(projectId)}/changes/${encodeURIComponent(csId)}`,
  );
}

/** 变更集生命周期动作：analyze / plan / approve / execute / review / merge。 */
export async function changeAction(
  projectId: string,
  csId: string,
  action:
    | "analyze"
    | "plan"
    | "approve"
    | "execute"
    | "review"
    | "merge"
    | "execute-agent",
  expectedHead: string,
  extra: Record<string, unknown> = {},
): Promise<unknown> {
  return request(
    `${projectPrefix(projectId)}/changes/${encodeURIComponent(csId)}/${action}`,
    {
      idempotency_key: key(),
      expected_head: expectedHead,
      ...extra,
    },
  );
}

export interface AuditFinding {
  code?: string;
  severity?: string;
  layer?: string;
  summary?: string;
  detail?: string;
  evidence?: unknown;
  [key: string]: unknown;
}

export interface AuditResult {
  findings: AuditFinding[];
  [key: string]: unknown;
}

export async function runAudit(projectId: string): Promise<AuditResult> {
  return request(`${projectPrefix(projectId)}/audit`, {
    idempotency_key: key(),
  });
}

// ---------------------------------------------------------------------------
// Agent 任务（协作主编 / 撰写 / 审计 / 风格观察）
// ---------------------------------------------------------------------------

export type WorkerRole =
  | "planner"
  | "writer"
  | "auditor"
  | "style_observer"
  | "coordinator";

export interface AgentJob {
  job_type?: string;
  acceptance?: { commit_id?: string };
  error?: unknown;
  job_id?: string;
  id?: string;
  state?: string;
  status?: string;
  payload?: {
    project_id?: string;
    role?: string;
    instruction?: string;
    chapter_id?: string;
    [key: string]: unknown;
  };
  output?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface CoordinateResult {
  decision: Record<string, unknown>;
  snapshot: StorySnapshot;
}

export async function coordinate(
  projectId: string,
  instruction: string,
): Promise<CoordinateResult> {
  return request(`${projectPrefix(projectId)}/coordinate`, { instruction });
}

export async function submitAgentJob(
  projectId: string,
  role: WorkerRole,
  options: {
    instruction?: string;
    chapterId?: string;
    targetIds?: string[];
    baseCommitId?: string;
    beforeCommitId?: string;
    tokenBudget?: number;
  } = {},
): Promise<AgentJob> {
  return request(`${projectPrefix(projectId)}/agent-jobs`, {
    role,
    idempotency_key: key(),
    instruction: options.instruction ?? "",
    chapter_id: options.chapterId ?? "",
    target_ids: options.targetIds ?? [],
    base_commit_id: options.baseCommitId,
    before_commit_id: options.beforeCommitId,
    token_budget: options.tokenBudget ?? 16000,
  });
}

export async function listAgentJobs(projectId: string): Promise<AgentJob[]> {
  const data = await request<{ jobs?: AgentJob[] } | AgentJob[]>(
    `${projectPrefix(projectId)}/agent-jobs`,
  );
  return Array.isArray(data) ? data : (data.jobs ?? []);
}

export async function fetchAgentJob(
  projectId: string,
  jobId: string,
): Promise<AgentJob> {
  return request(
    `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobId)}`,
  );
}

export async function fetchAgentJobEvents(
  projectId: string,
  jobId: string,
): Promise<Record<string, unknown>[]> {
  const data = await request<
    { events?: Record<string, unknown>[] } | Record<string, unknown>[]
  >(
    `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobId)}/events`,
  );
  return Array.isArray(data) ? data : (data.events ?? []);
}

export async function runAgentJob(
  projectId: string,
  jobId: string,
): Promise<AgentJob> {
  return request(
    `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobId)}/run`,
    {
      idempotency_key: key(),
    },
  );
}

export async function cancelAgentJob(
  projectId: string,
  jobId: string,
): Promise<AgentJob> {
  return request(
    `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobId)}/cancel`,
    { idempotency_key: key() },
  );
}

export async function acceptAgentJob(
  projectId: string,
  jobId: string,
  expectedHead: string,
): Promise<unknown> {
  return request(
    `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobId)}/accept`,
    {
      idempotency_key: key(),
      expected_head: expectedHead,
    },
  );
}
