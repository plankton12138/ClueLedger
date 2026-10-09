/**
 * 工具卡片摘要（P7 T3，计划 D4）：把 AgentToolResult.details 摊平成
 * 键值行列表，供对话流工具卡片完成后直接展示（不必展开原始 JSON）。
 * 纯函数零 DOM；单元格口径与 BiblePanel 的 cellText 同款（数组顿号连接、
 * 布尔是/否、空值占位「—」），超长截断。
 */

export interface ToolDetailLine {
  label: string;
  value: string;
}

const CELL_MAX = 80;

function truncateCell(text: string): string {
  return text.length > CELL_MAX ? `${text.slice(0, CELL_MAX)}…` : text;
}

/** 单值 → 展示文案（空值/空数组都是「—」：合法空结果，不装作有内容）。 */
function summarizeCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    return value.length === 0 ? "—" : truncateCell(value.map(summarizeCell).join("、"));
  }
  if (typeof value === "object") return truncateCell(JSON.stringify(value));
  return truncateCell(String(value));
}

/**
 * details → 摘要行。顶层 record 摊平为逐字段行；顶层非 record
 * （字符串/数组等）包成单行「结果」；null/undefined → 空数组（卡片不渲染该区）。
 * 空对象同样返回空数组（无字段可展示 ≠ 出错）。
 */
export function toolDetailLines(details: unknown): ToolDetailLine[] {
  if (details === null || details === undefined) return [];
  if (Array.isArray(details)) {
    return [{ label: "结果", value: summarizeCell(details) }];
  }
  if (typeof details === "object") {
    return Object.entries(details as Record<string, unknown>).map(([label, value]) => ({
      label,
      value: summarizeCell(value),
    }));
  }
  return [{ label: "结果", value: summarizeCell(details) }];
}
