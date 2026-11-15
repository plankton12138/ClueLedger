import type { TraceEvent } from "../../api/activity";

export const kinds: Record<TraceEvent["kind"], string> = {
  loop: "Loop",
  model: "Model",
  tool_call: "Tool call",
  gate: "Gate",
  retrieval: "Retrieval",
  write: "Write",
  reply: "Reply",
};

export const statuses: Record<string, string> = {
  completed: "已完成",
  recovered: "已恢复",
  completed_with_errors: "含失败",
  failed: "失败",
  passed: "通过",
  blocked: "拦截",
  skipped: "跳过",
  running: "进行中",
  stopped: "已停止",
  interrupted: "已中断",
  paused: "已暂停",
  needs_input: "等待作者",
  truncated: "已截断",
  partial: "部分完成",
};

export const duration = (ms: number | null) =>
  ms === null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

export const localTime = (at: string | null) =>
  at ? new Date(at).toLocaleString("zh-CN", { hour12: false }) : "—";

export const statusLabel = (status: string) => statuses[status] ?? status;

export const failedStatuses = new Set([
  "failed",
  "blocked",
  "completed_with_errors",
  "interrupted",
]);
