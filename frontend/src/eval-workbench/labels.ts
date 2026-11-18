import type { CheckSummary, JobPayload } from "./api";

const CASE_NAMES: Record<string, string> = {
  revise_ch1_ending_farm_tonight: "第一章结尾改稿",
  revise_ch3_ending_snow: "第三章结尾改稿",
};

export function caseName(id: string) {
  return CASE_NAMES[id] || id;
}

export function checkLabels(checks?: CheckSummary) {
  if (!checks) return [];
  const { required, pending, insufficient, rejected, quality, diagnostic = 0, abnormal } = checks;
  return [
    { count: required, label: "项必须检查失败", tone: "danger" },
    { count: pending, label: "项待你确认", tone: "review" },
    { count: insufficient, label: "项待补证", tone: "review" },
    { count: rejected, label: "项作者复核未通过", tone: "warning" },
    { count: quality, label: "项质量检查未通过", tone: "warning" },
    { count: diagnostic, label: "项诊断异常", tone: "warning" },
    { count: abnormal, label: "次异常或未运行", tone: "danger" },
  ].filter(item => item.count > 0).map(item => ({
    text: `${item.count} ${item.label}`, tone: item.tone,
  }));
}

export function jobIsActive(job: JobPayload) {
  return ["queued", "running", "cancelling"].includes(job.status);
}

export function jobStatusLabel(status: string) {
  return ({
    queued: "等待运行", running: "运行中", cancelling: "正在取消",
    completed: "已完成", failed: "运行异常", cancelled: "已取消",
  } as Record<string, string>)[status] || status;
}

export function namedRunDate(id: string): Date | null {
  const parts = id.match(/(?:^|-)(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  if (!parts) return null;
  const [, year, month, day, hour, minute, second] = parts.map(Number);
  const date = new Date(year, month - 1, day, hour, minute, second);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
    && date.getHours() === hour && date.getMinutes() === minute && date.getSeconds() === second
    ? date : null;
}

export function runDate(id: string, startedAt: unknown): Date | null {
  if (typeof startedAt === "string") {
    const date = new Date(startedAt);
    if (Number.isFinite(date.getTime())) return date;
  }
  return namedRunDate(id);
}

/** 界面展示用；落盘仍用 smoke / gate。 */
export function modeLabel(mode: string | null | undefined): string {
  if (mode === "gate") return "正式验收 · 每案 3 次";
  if (mode === "smoke") return "快速检查 · 每案 1 次";
  return mode || "—";
}

export function modeShort(mode: string | null | undefined): string {
  if (mode === "gate") return "正式验收";
  if (mode === "smoke") return "快速检查";
  return mode || "—";
}

export function gateLabel(passed: boolean | null | undefined): string {
  if (passed == null) return "—";
  return passed ? "验收通过" : "验收未通过";
}

export const STAGE_LABELS: Record<string, string> = {
  scope: "范围登记",
  read: "读取原文",
  candidate: "提交候选",
  verify: "应用核对",
  repair: "修补",
  commit: "提交",
};

export function stageLabel(id: string | null | undefined): string {
  if (!id) return "无";
  return STAGE_LABELS[id] || id;
}

export function levelLabel(level: string | null | undefined): string {
  if (level === "must") return "必须";
  if (level === "quality") return "质量";
  if (level === "human") return "人工";
  return level || "—";
}

export function lensLabel(lens: string): string {
  if (lens === "C") return "组件";
  if (lens === "I") return "集成";
  if (lens === "O") return "结果";
  if (lens === "A") return "归因";
  return lens;
}
