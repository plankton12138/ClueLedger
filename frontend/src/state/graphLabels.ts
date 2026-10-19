/**
 * 图谱文案（V03-6.5）：语义层、线索状态、证据状态与关系概述的中文标签。
 *
 * 设计依据（产品 §5.8）：画布、详情、文字目录是同一份投影的三种读法，标签口径必须
 * 一致——同一对象在画布上叫「真相」，在目录里不能变成别的说法。因此标签抽成纯函数
 * 模块，由 GraphDetail 与 GraphDirectory 共用，不各自维护一份。
 */

import type { ClueStatus, GraphEdgeWire, GraphNodeWire } from "../api/graphs";

const LAYER_LABELS: Record<string, string> = {
  truth: "真相",
  knowledge: "认知",
  disclosure: "披露",
  plan: "计划",
};

/** 语义层标签：作者一眼分清客观事实与角色理解。 */
export function layerLabel(layer: string): string {
  return LAYER_LABELS[layer] ?? layer;
}

const CLUE_STATUS_LABELS: Record<ClueStatus, string> = {
  implemented: "已在正文出现",
  planned: "计划出现，尚未写入正文",
  missing: "还没有关联出现记录",
};

/** 线索状态标签：正文实现 / 计划 / 无出现记录，三态不混用。 */
export function clueStatusLabel(status: string): string {
  return CLUE_STATUS_LABELS[status as ClueStatus] ?? status;
}

/** 证据状态徽章：来源有效 / 需核对 / 未记录。 */
export function evidenceLabel(status: string): {
  text: string;
  tone: "green" | "amber" | "blue";
} {
  if (status === "needs_check") return { text: "来源需核对", tone: "amber" };
  if (status === "valid") return { text: "来源有效", tone: "green" };
  return { text: "未记录依据", tone: "blue" };
}

/**
 * 关系概述：`源 → 目标` 加表面/真实关系；角色视角下真实关系被隐藏时如实标「当前视角不可见」，
 * 不用空白让人误以为没有真实关系。
 */
export function relationSummary(
  edge: GraphEdgeWire,
  nodesById: Record<string, GraphNodeWire>,
): string {
  const source = nodesById[edge.source]?.title ?? edge.source;
  const target = nodesById[edge.target]?.title ?? edge.target;
  const parts = [`${source} → ${target}`];
  if (edge.public_state) parts.push(`表面关系：${edge.public_state}`);
  if (edge.true_state_hidden) parts.push("真实关系：当前视角不可见");
  else if (edge.true_state) parts.push(`真实关系：${edge.true_state}`);
  return parts.join(" · ");
}
