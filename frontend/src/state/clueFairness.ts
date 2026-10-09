/**
 * 线索公平性派生（P7 T3，计划 D4 概览卡）：与审计中心（T8，D7）同口径——
 * 非红鲱鱼线索中已呈现（status != planned）占比。数据来自面板已加载的
 * 线索账本实体（BiblePanel 启动即五账本全拉），零新端点；纯函数可测。
 */

export interface ClueFairness {
  /** 非红鲱鱼线索中已呈现条数。 */
  presented: number;
  /** 非红鲱鱼线索总数（分母不含红鲱鱼）。 */
  total: number;
  /** presented / total（0..1）。 */
  rate: number;
}

/**
 * 线索账本实体 → 公平性；空账本（或全为红鲱鱼）→ null（合法空结果，
 * 与 T8 审计端点 metrics.clue_fairness 的 None 口径一致）。status 缺失
 * 或空串按 planned 处理（未登记呈现状态 ≠ 已呈现）。
 */
export function clueFairness(
  entities: readonly Record<string, unknown>[],
): ClueFairness | null {
  const genuine = entities.filter((e) => e["is_red_herring"] !== true);
  const total = genuine.length;
  if (total === 0) return null;
  const presented = genuine.filter((e) => {
    const status = e["status"];
    return typeof status === "string" && status !== "" && status !== "planned";
  }).length;
  return { presented, total, rate: presented / total };
}
