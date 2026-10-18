/**
 * 操作后的后续状态（V03-2.6）：摘要是否待更新、账本建议是否已整理。
 *
 * 只读查询：服务端按受影响章节的摘要状态确定性回答，不创建任务、不调用模型。
 */

import { projectPrefix, request } from "./v2";

export type SummaryState = "missing" | "stale" | "fresh";
export type SuggestionsState = "not_started" | "available" | "handled";

export interface OperationFollowups {
  operation_id: string;
  status: string;
  changed_chapter_ids: string[];
  summary_state: SummaryState;
  ledger_suggestions_state: SuggestionsState;
  result_url?: string;
}

export async function fetchOperation(
  projectId: string,
  operationId: string,
): Promise<OperationFollowups> {
  return request(
    `${projectPrefix(projectId)}/operations/${encodeURIComponent(operationId)}`,
  );
}

export function summaryLabel(state: SummaryState): string {
  if (state === "missing") return "还没有这一章的摘要";
  if (state === "stale") return "摘要待更新（正文已变化）";
  return "摘要与正文一致";
}

export function suggestionsLabel(state: SuggestionsState): string {
  if (state === "available") return "有账本建议可查看";
  if (state === "handled") return "账本建议已处理";
  return "尚未整理账本建议";
}
