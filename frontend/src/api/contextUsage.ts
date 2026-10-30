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
  /** 自动压缩策略；说明「什么时候需要压缩」，不是容量事实。 */
  policy: CompactionPolicy;
  diagnostics: string[];
  revision: string;
  compaction?: CompactionOperation | null;
}

export interface CompactionPolicy {
  auto_compaction: boolean;
  /** 可用输入预算的比例；触发点据此计算。 */
  threshold: number;
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
 * 运行中采用请求冻结的容量，空闲时读取当前容量；切换模型后等待新的输入估算。
 *
 * 三个上限分开表达：`project_input_limit_tokens` 是项目策略，`model_input_limit_tokens`
 * 是模型允许的输入，`input_limit_tokens` 是两者较小值——主进度只用最后这个。
 */
export interface ContextView {
  status: "pending" | "running" | "ready";
  estimated_active_tokens: number | null;
  last_request_input_tokens: number | null;
  context_window_tokens: number | null;
  input_limit_tokens: number | null;
  project_input_limit_tokens: number | null;
  model_input_limit_tokens: number | null;
  input_limit_source: InputLimitSource;
  /** 连接与精确模型 ID；不含密钥或账号凭据。 */
  model_identity: string;
  output_reserve_tokens: number | null;
  safety_tokens: number | null;
  /** 窗口数字的出处；展开明细时显示，避免把目录值当成作者填的。 */
  capacity_source: CapacitySource;
  budget: "unknown" | "ok" | "near" | "over";
  instructions_tokens: number | null;
  tools_tokens: number | null;
  conversation_tokens: number | null;
  method: string;
  fingerprint: string;
  limits_revision: string;
  policy_revision: string;
}

export type CapacitySource = "override" | "metadata" | "catalog" | "unknown";

/** 有效输入预算最后由谁决定；未知模型时只能是项目预算。 */
export type InputLimitSource = "project" | "model" | "both";

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

/** 占比只按有效输入预算算：模型窗口放明细，不再当主分母。 */
export function contextShare(context: ContextView): number | null {
  if (!context.input_limit_tokens || context.estimated_active_tokens === null) {
    return null;
  }
  return context.estimated_active_tokens / context.input_limit_tokens;
}

export function formatPercent(share: number | null): string {
  if (share === null) {
    return "";
  }
  const percent = share * 100;
  return `${percent < 1 ? percent.toFixed(1) : Math.round(percent)}%`;
}

/** 浮层内的精确数字：加千分位，避免 128000 与 12800 看混。 */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 有有效输入预算才谈占比与进度条；只有已用量没有分母时不猜。 */
export function hasCapacity(context: ContextView): boolean {
  return context.input_limit_tokens !== null && context.input_limit_tokens > 0;
}

/** 浮层首行：已用与有效输入预算；没有预算时如实说未知，不画进度条。 */
export function contextAmount(context: ContextView): string {
  const used = context.estimated_active_tokens;
  const limit = context.input_limit_tokens;
  if (used === null) {
    // 还没有估算时给分母，不给 0%：缺估算不等于没占用。
    return limit === null ? "—" : `待估算 / ${formatCount(limit)} tokens`;
  }
  if (!hasCapacity(context) || limit === null) {
    return `≈${formatCount(used)} tokens · 输入预算未知`;
  }
  return `≈${formatCount(used)} / ${formatCount(limit)} tokens`;
}

export const CAPACITY_SOURCE_LABEL: Record<CapacitySource, string> = {
  override: "旧配置（已停用）",
  metadata: "服务商返回",
  catalog: "内置目录",
  unknown: "未知",
};

export function capacitySourceLabel(source: CapacitySource | string): string {
  return CAPACITY_SOURCE_LABEL[source as CapacitySource] ?? "未知";
}

export const INPUT_LIMIT_SOURCE_LABEL: Record<InputLimitSource, string> = {
  project: "项目预算",
  model: "模型容量",
  both: "项目与模型相同",
};

export function inputLimitSourceLabel(source: InputLimitSource | string): string {
  return INPUT_LIMIT_SOURCE_LABEL[source as InputLimitSource] ?? "项目预算";
}

/**
 * 模型窗口一行。
 *
 * 窗口未知时说明「按项目预算控制」，而不是让整个上下文状态停在未知：
 * 预算仍然成立，只是没有模型那一侧的换算。
 */
export function modelWindowNote(context: ContextView): string {
  if (context.context_window_tokens === null) {
    return "未获取；按项目预算控制";
  }
  return `${formatCount(context.context_window_tokens)} tokens`;
}

/** 模型可用输入只在窗口与输入换算都存在时出现，不伪造未知字段。 */
export function modelInputNote(context: ContextView): string | null {
  if (context.context_window_tokens === null || context.model_input_limit_tokens === null) {
    return null;
  }
  return `${formatCount(context.model_input_limit_tokens)} tokens`;
}

/**
 * 自动压缩的触发点。
 *
 * 阈值是有效输入预算的比例，不是窗口比例：压缩本身也要预留空间。
 * 预算未知或自动压缩关闭时不给数字，避免给出一个不存在的边界。
 */
export function autoCompactionNote(
  context: ContextView,
  policy: CompactionPolicy | null | undefined,
): string | null {
  if (!policy) {
    return null;
  }
  if (!policy.auto_compaction) {
    return "自动压缩已关闭";
  }
  if (context.input_limit_tokens === null || context.input_limit_tokens <= 0) {
    return null;
  }
  const trigger = Math.ceil(context.input_limit_tokens * policy.threshold);
  return `达到 ${formatCount(trigger)} tokens（有效输入预算的 ${Math.round(
    policy.threshold * 100,
  )}%）时自动压缩`;
}

/** 上下文入口的常驻文案：先是状态，再是数字。 */
export function contextLabel(context: ContextView | null): string {
  if (!context || context.status === "pending" || context.estimated_active_tokens === null) {
    return "上下文 待计算";
  }
  const tokens = `≈${formatTokens(context.estimated_active_tokens)}`;
  const share = contextShare(context);
  // 没有分母时把单位说清楚，避免「约 2716」被当成百分比或字数。
  return share === null ? `上下文 ${tokens} tokens` : `上下文 ${formatPercent(share)}`;
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
 * 手动压缩操作；`completed` 时才带记录，`failed` 时带错误。
 *
 * 压缩不是创作轮次：它没有作者消息，也不产生作品提交，所以单独一套状态。
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

/** 压缩结束后的简短说明；成功给前后规模，失败说原因，取消说没变化。 */
export function compactionNote(operation: CompactionOperation): string {
  if (operation.state === "completed") {
    const before = formatTokens(operation.record?.estimated_before ?? null);
    const after = formatTokens(operation.record?.estimated_after ?? null);
    return `已压缩：${before} → ${after}`;
  }
  if (operation.state === "noop") {
    return operation.note || "当前没有需要压缩的较早内容。";
  }
  if (operation.state === "failed") {
    const error = operation.error;
    return error ? `[${error.code}] ${error.where}：${error.why}。建议：${error.fix}。`
      : "[COMPACTION_FAILED] 上下文压缩：这次没有压缩。建议：保留当前上下文，稍后重试。";
  }
  if (operation.state === "stopped") {
    return operation.note || "压缩已取消；当前上下文没有变化。";
  }
  return "";
}
