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
  context: ContextView;
  diagnostics: string[];
  revision: string;
  compaction?: CompactionOperation | null;
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

/**
 * 当前有效上下文；`null` 表示这一项没有可靠数值。
 *
 * `pending` 还没有请求，`running` 有一轮正在跑，`ready` 最近一轮已结束。
 * 容量取自最近一次请求受理时冻结的那份，与真正发出去的请求一致。
 */
export interface ContextView {
  status: "pending" | "running" | "ready";
  estimated_active_tokens: number | null;
  last_request_input_tokens: number | null;
  context_window_tokens: number | null;
  input_limit_tokens: number | null;
  output_reserve_tokens: number | null;
  budget: "unknown" | "ok" | "near" | "over";
  instructions_tokens: number | null;
  tools_tokens: number | null;
  conversation_tokens: number | null;
  method: string;
  fingerprint: string;
  limits_revision: string;
}

/** 一万以下给原数，以上给「万」：窄栏里长数字会挤掉发送按钮。 */
export function formatTokens(value: number | null): string {
  if (value === null) {
    return "—";
  }
  if (value < 10_000) {
    return String(value);
  }
  const scaled = value / 10_000;
  return `${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1)} 万`;
}

/** 占比只按窗口算；没有窗口就是没有占比，不做别的分母。 */
export function contextShare(context: ContextView): number | null {
  if (!context.context_window_tokens || context.estimated_active_tokens === null) {
    return null;
  }
  return context.estimated_active_tokens / context.context_window_tokens;
}

export function formatPercent(share: number | null): string {
  if (share === null) {
    return "";
  }
  const percent = share * 100;
  return `${percent < 1 ? percent.toFixed(1) : Math.round(percent)}%`;
}

/** 上下文入口的常驻文案：先是状态，再是数字。 */
export function contextLabel(context: ContextView | null): string {
  if (!context || context.status === "pending" || context.estimated_active_tokens === null) {
    return "上下文 待计算";
  }
  const tokens = `≈${formatTokens(context.estimated_active_tokens)}`;
  const share = contextShare(context);
  return share === null ? `上下文 ${tokens}` : `上下文 ${formatPercent(share)}`;
}

/** 累计入口的常驻文案；零、未知、部分必须分开说。 */
export function usageLabel(totals: UsageTotals | null): string {
  if (!totals) {
    return "累计 —";
  }
  const state = usageDisplayState(totals);
  if (state === "empty") {
    return "累计 0";
  }
  if (state === "pending") {
    return "累计 统计中";
  }
  if (state === "unknown") {
    return "累计 未知";
  }
  const known = `累计 ${formatTokens(totals.total_tokens)}`;
  return state === "partial" ? `${known}（部分未统计）` : known;
}

export interface BreakdownItem {
  key: "instructions" | "tools" | "conversation";
  label: string;
  tokens: number | null;
}

/** 浮层里的三项构成：只列这三项，回复预留单独说明。 */
export function breakdown(context: ContextView): BreakdownItem[] {
  return [
    { key: "instructions", label: "约定与资料", tokens: context.instructions_tokens },
    { key: "tools", label: "工具定义", tokens: context.tools_tokens },
    { key: "conversation", label: "对话与结果", tokens: context.conversation_tokens },
  ];
}

/** 请求耗时之和，不是从打开会话到现在的时长。 */
export function formatDuration(ms: number | null): string {
  if (ms === null) {
    return "—";
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)} 秒`;
  }
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} 分 ${Math.round((ms % 60_000) / 1000)} 秒`;
}

/**
 * 手动整理操作；`completed` 时才带记录，`failed` 时带错误。
 *
 * 整理不是创作轮次：它没有作者消息，也不产生作品提交，所以单独一套状态。
 */
export interface CompactionOperation {
  operation_id: string;
  conversation_id: string;
  state: "queued" | "running" | "completed" | "noop" | "failed" | "stopped";
  record: {
    compaction_id: string;
    first_kept_turn_id: string;
    estimated_before: number;
    estimated_after: number;
  } | null;
  error: { code: string; where: string; why: string; fix: string } | null;
  note: string;
}

/** 终态之后不再轮询。 */
export function isCompactionFinished(operation: CompactionOperation): boolean {
  return ["completed", "noop", "failed", "stopped"].includes(operation.state);
}

/** 整理结束后的简短说明；成功给前后规模，失败说原因，取消说没变化。 */
export function compactionNote(operation: CompactionOperation): string {
  if (operation.state === "completed") {
    const before = formatTokens(operation.record?.estimated_before ?? null);
    const after = formatTokens(operation.record?.estimated_after ?? null);
    return `已整理：${before} → ${after}`;
  }
  if (operation.state === "noop") {
    return operation.note || "当前没有需要整理的较早内容。";
  }
  if (operation.state === "failed") {
    const error = operation.error;
    return error ? `[${error.code}] ${error.where}：${error.why}。建议：${error.fix}。`
      : "[COMPACTION_FAILED] 上下文整理：这次没有整理。建议：保留当前上下文，稍后重试。";
  }
  if (operation.state === "stopped") {
    return operation.note || "整理已取消；当前上下文没有变化。";
  }
  return "";
}
