/** 资料页共用的状态文案与色调：覆盖与有效性分开表达，不合成一个「状态」。 */

import type { CoverageOutcome, Freshness, MaterialTaskStatus } from "../../api/storyMaterials";

export const COVERAGE_LABEL: Record<CoverageOutcome, string> = {
  found: "有结果",
  empty: "无发现",
  insufficient: "信息不足",
  unrecorded: "未整理",
};

export const COVERAGE_TONE: Record<CoverageOutcome, string> = {
  found: "text-green",
  empty: "text-blue",
  insufficient: "text-amber",
  unrecorded: "text-mute",
};

export const FRESHNESS_LABEL: Record<Freshness, string> = {
  valid: "",
  stale: "来源已修改",
  source_missing: "来源已删除",
  unavailable: "无法读取",
  none: "",
};

export const FRESHNESS_TONE: Record<Freshness, string> = {
  valid: "text-mute",
  stale: "text-amber",
  source_missing: "text-amber",
  unavailable: "text-red",
  none: "text-mute",
};

export const TASK_LABEL: Record<MaterialTaskStatus, string> = {
  accepted: "已受理",
  running: "整理中",
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  stopped: "已停止",
  interrupted: "中断",
};
