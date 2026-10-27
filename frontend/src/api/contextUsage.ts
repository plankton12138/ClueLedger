/**
 * 会话累计用量视图（0.9 U0 契约）。
 *
 * 字段名与服务端 `workspace/conversation_usage.py` 的 `UsageTotals` 完全一致，
 * 两侧改名必须同时改。这里只定义「拿到数字之后怎么解释」，聚合与估算仍由服务端负责：
 * 前端不再自己累计一套账，否则刷新一次就会和实时推送对不上。
 *
 * 三个数字容易混：
 * - `finished_requests` 是已经结束的真实请求数，失败与停止的请求也计入；
 * - `requests_without_usage` 说明其中多少条没有可靠用量；
 * - `requests_in_progress` 是已经发出、还没有终态的请求。
 */

export interface UsageTotals {
  /** 已知累计总量；没有任何可靠用量时为 null，不等于零。 */
  total_tokens: number | null;
  /** 含缓存读写的完整输入。 */
  input_tokens: number | null;
  /** 已包含协议归入输出的推理。 */
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  finished_requests: number;
  requests_without_usage: number;
  requests_in_progress: number;
  /** 请求耗时之和；不是从打开会话到现在的时长。 */
  model_duration_ms: number | null;
}

/** 没有请求的会话显示零；有用量缺失的会话显示未知，两者不能合并。 */
export type UsageDisplayState = "empty" | "pending" | "unknown" | "partial" | "known";

/**
 * `GET /api/file-workspaces/{id}/conversations/{id}/usage` 的响应。
 *
 * `through_seq` 与 `diagnostics` 一起到达：前者判断视图是否落后，后者说明记录链路
 * 是否出现过异常（例如同一请求两个终态）。诊断不影响数字，但不能忽略。
 */
export interface ConversationUsageView {
  conversation_id: string;
  through_seq: number;
  usage: UsageTotals;
  diagnostics: string[];
  revision: string;
}

export function usageDisplayState(totals: UsageTotals): UsageDisplayState {
  if (totals.finished_requests === 0 && totals.requests_in_progress === 0) {
    return "empty";
  }
  if (totals.finished_requests === 0) {
    return "pending";
  }
  if (totals.total_tokens === null) {
    return "unknown";
  }
  return totals.requests_without_usage > 0 ? "partial" : "known";
}
