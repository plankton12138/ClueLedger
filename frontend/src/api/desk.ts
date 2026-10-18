/**
 * 工作台共享状态（V03-4.5）：侧栏只拿计数与权威 ID，不复制列表。
 *
 * 设计依据（产品设计 §5.1/§5.2、§6）：
 * - 待确认只显示「有几条」，条目正文回候选组接口取；
 * - 最近结果按权威操作记录投影，重开页面或换讨论都还在；
 * - `content_hash` 用来判断要不要重取，不靠逐字段比对或定时轮询。
 */

import type { OperationEnvelope } from "./identities";
import { projectPrefix, request } from "./v2";

export interface DeskCounts {
  ledger_entries: number;
  agreements: number;
  summaries: { fresh: number; stale: number; missing: number };
  pending_candidates: number;
  hypothesis_candidates: number;
  open_edit_chapters: number;
}

export interface RecentOperation {
  operation_id: string;
  kind: string;
  status: string;
  conversation_id?: string | null;
  changed_chapter_ids: string[];
  created_at: string;
}

export interface DeskState {
  project_id: string;
  commit_id: string | null;
  counts: DeskCounts;
  recent_operations: RecentOperation[];
  refs: {
    candidate_group_id: string | null;
    candidate_revision: number | null;
    agreements_hash: string;
    edit_session_id: string | null;
    edit_chapter_ids: string[];
  };
  content_hash: string;
}

export async function fetchDeskState(projectId: string): Promise<DeskState> {
  return request(`${projectPrefix(projectId)}/desk-state`);
}

/**
 * 最近结果转成与实时事件同一形状：侧栏因此只有一份列表缓存，两种来源按
 * operationId 合并，不会出现「实时一条、历史一条」。
 */
export function toOperationEnvelope(
  projectId: string,
  row: RecentOperation,
): OperationEnvelope {
  return {
    operationId: row.operation_id,
    status: row.status as OperationEnvelope["status"],
    projectId,
    conversationId: row.conversation_id ?? null,
    commitId: null,
    changedChapterIds: row.changed_chapter_ids,
  };
}

/** 侧栏标题上的待确认数字；0 时不显示（空列表不占一行）。 */
export function pendingLabel(count: number): string {
  return count > 0 ? `待确认 ${count}` : "";
}
