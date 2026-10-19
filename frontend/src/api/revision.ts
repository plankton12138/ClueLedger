/**
 * 整体调整客户端（V03-7.3/7.4 + 执行循环）：任务账本、覆盖台账、暂存与发布。
 *
 * 契约点：
 * - 开任务不调用模型；预算只能缩小默认值（`call_budget` 缺省 12）；
 * - 执行一轮逐章先记账再调用模型；预算不足时任务落 `waiting/budget_exhausted`；
 * - 同一个 `request_id` 重放返回原摘要，不重复记账/调用；
 * - 发布是一次提交：正文与账本进同一个 Commit，门禁不通过时整体拒绝。
 */

import { projectPrefix, request } from "./v2";

export interface RevisionBudget {
  call_limit: number;
  calls_used: number;
  remaining: number;
  repair_rounds: number;
  max_repair_rounds: number;
  exhausted: boolean;
}

export interface PublicationBlocker {
  code: string;
  why: string;
  fix: string;
}

export interface RevisionUncoveredRow {
  chapter_id: string;
  status: string;
  reason: string;
}

export interface RevisionCoverageRow {
  chapter_id: string;
  status: string;
  source_revision_id?: string;
  text_length?: number;
}

export interface RevisionTask {
  task_id: string;
  project_id: string;
  work_id: string;
  base_commit_id: string;
  state: string;
  budget: RevisionBudget;
  waiting_reason: string;
  waiting_detail: string;
  requires_revalidation: boolean;
  published_operation_id: string;
  published_commit_id: string;
  coverage: {
    work_id: string;
    complete: boolean;
    covered_count: number;
    total_count: number;
    uncovered: RevisionUncoveredRow[];
  } | null;
  publication?: {
    ready: boolean;
    blockers: PublicationBlocker[];
    head_commit_id: string | null;
  };
}

export interface RevisionChapter {
  chapter_id: string;
  title: string;
  order_key: string;
  chapter_base: string;
}

export interface RevisionWork {
  work_id: string | null;
  opened?: boolean;
  base_commit_id?: string;
  coverage?: RevisionCoverageRow[];
  complete?: boolean;
  covered_count?: number;
  total_count?: number;
  uncovered?: RevisionUncoveredRow[];
  context?: { chapters: RevisionChapter[] };
}

export interface RevisionRunChapter {
  chapter_id: string;
  candidate_id: string;
  chars: number;
  calls_used: number | null;
}

export interface RevisionRunResult {
  status: "executed" | "stopped" | "noop" | "replayed";
  task_id: string;
  request_id: string;
  chapters: RevisionRunChapter[];
  stopped: { reason: string; chapter_id: string; why?: string } | null;
  task: RevisionTask;
}

export interface StagedChapter {
  chapter_id: string;
  base_revision_id: string;
  text: string;
  updated_at: string;
}

export interface StagedView {
  task_id: string;
  chapters: StagedChapter[];
  ledger: unknown[];
  chapter_count: number;
  ledger_count: number;
  empty: boolean;
  updated_at?: string;
}

const prefix = (projectId: string) => `${projectPrefix(projectId)}/revision-tasks`;

/** 当前任务；没有时返回 `{ task_id: null }`，不是错误。 */
export async function fetchCurrentTask(
  projectId: string,
): Promise<RevisionTask | null> {
  const body = await request<RevisionTask & { task_id: string | null }>(
    `${prefix(projectId)}/current`,
  );
  return body.task_id === null ? null : body;
}

export async function fetchTask(
  projectId: string,
  taskId: string,
): Promise<RevisionTask> {
  return request<RevisionTask>(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}`,
  );
}

export async function startTask(
  projectId: string,
  body: { request_id: string; call_budget?: number },
): Promise<RevisionTask> {
  return request<RevisionTask>(prefix(projectId), body);
}

/** 执行一轮；`chapter_ids` 为空时服务端只跑未下结论的章。 */
export async function runRound(
  projectId: string,
  taskId: string,
  body: { instruction: string; chapter_ids?: string[]; request_id: string },
): Promise<RevisionRunResult> {
  return request<RevisionRunResult>(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}/run`,
    { instruction: body.instruction, chapter_ids: body.chapter_ids ?? [], request_id: body.request_id },
  );
}

export async function continueTask(
  projectId: string,
  taskId: string,
  additionalCalls: number,
): Promise<RevisionTask> {
  return request<RevisionTask>(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}/continue`,
    { additional_calls: additionalCalls },
  );
}

export async function stopTask(
  projectId: string,
  taskId: string,
): Promise<RevisionTask> {
  return request<RevisionTask>(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}/stop`,
    {},
  );
}

export async function publishTask(
  projectId: string,
  taskId: string,
  operationId: string,
): Promise<{ status: string; operation_id: string; commit_id: string | null; changed_chapters: string[] }> {
  return request(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}/publish`,
    { operation_id: operationId },
  );
}

export async function fetchStaged(
  projectId: string,
  taskId: string,
): Promise<StagedView> {
  return request<StagedView>(
    `${prefix(projectId)}/${encodeURIComponent(taskId)}/staged`,
  );
}

export async function fetchRevisionWork(
  projectId: string,
): Promise<RevisionWork> {
  return request<RevisionWork>(`${projectPrefix(projectId)}/revision-work`);
}
