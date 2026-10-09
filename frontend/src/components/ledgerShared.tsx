/**
 * 账本共享件（P7 T5，计划 D5）：BiblePanel 与 LedgerPage 的公共沉淀——
 * 伏笔状态标签 / 风险语义色 / RiskCell / 五账本列定义 / 全页筛选排序纯函数。
 * 从 BiblePanel 原地提取（P6 T8 的 foreshadow_risks join 模式），双侧消费
 * 防漂移；纯函数用 vitest 钉住（渲染冒烟走浏览器人工验收，项目惯例）。
 */

import type { BibleType } from "../api/rest";

/** 伏笔状态分布中文标签与语义色（统计卡 chips / 状态筛选器共用）。 */
export const FORESHADOW_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  planned: { label: "已计划", color: "text-planned" },
  planted: { label: "已埋设", color: "text-planted" },
  echoed: { label: "已呼应", color: "text-gold-soft" },
  resolved: { label: "已回收", color: "text-recovered" },
};

/** 风险语义色：逾期红 / 临近暗金 / 正常绿，与账本生命周期色系同源。 */
export const RISK_META: Record<string, { label: string; chip: string }> = {
  overdue: { label: "逾期", chip: "border-overdue/60 text-overdue" },
  near: { label: "临近", chip: "border-gold/60 text-gold" },
  normal: { label: "正常", chip: "border-planted/60 text-planted" },
};

/** 伏笔生命周期着色：未回收（非 resolved）红点，已回收绿点。 */
export function foreshadowDot(status: unknown): string {
  return status === "resolved" ? "bg-planted" : "bg-overdue";
}

export function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join("、") : "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

/** 从章节 id（ch03）提取章号；非 chNN 形态返回 null（进度条不可判定）。 */
export function parseChapterNumber(chapterId: string): number | null {
  const match = /^ch(\d+)$/i.exec(chapterId.trim());
  return match === null ? null : Number(match[1]);
}

/** 五账本列定义（BiblePanel 与 LedgerPage 同表同列，防双处漂移）。 */
export const COLUMNS: Record<BibleType, Array<{ key: string; label: string }>> = {
  character: [
    { key: "name", label: "名字" },
    { key: "identity", label: "身份" },
    { key: "motivation", label: "动机" },
    { key: "secret", label: "秘密" },
    { key: "arc", label: "弧光" },
  ],
  clue: [
    { key: "name", label: "名字" },
    { key: "chapter", label: "章节" },
    { key: "points_to", label: "指向" },
    { key: "is_red_herring", label: "红鲱鱼" },
    { key: "status", label: "状态" },
  ],
  foreshadow: [
    { key: "name", label: "名字" },
    { key: "plant_chapter", label: "埋设章" },
    { key: "plant_mode", label: "方式" },
    { key: "expected_resolve_chapter", label: "预计回收" },
    { key: "status", label: "状态" },
    { key: "risk", label: "风险" },
  ],
  method: [
    { key: "name", label: "名字" },
    { key: "time_window", label: "时间窗" },
    { key: "status", label: "状态" },
  ],
  timeline: [
    { key: "name", label: "事件" },
    { key: "day", label: "第几天" },
    { key: "t_start", label: "开始" },
    { key: "t_end", label: "结束" },
    { key: "participants", label: "参与人" },
  ],
};

/** 章节排序键：chNN 数值序，不可解析的排到最后（合法条目不被坏锚点拖乱）。 */
function chapterSortKey(value: unknown): number {
  const n = parseChapterNumber(String(value ?? ""));
  return n === null ? Number.MAX_SAFE_INTEGER : n;
}

/** 全页查询条件（LedgerPage 工具行）。 */
export interface LedgerQuery {
  /** 本地过滤：任一字段值包含（大小写不敏感）。 */
  search: string;
  /** 伏笔状态筛选；"all" 不过滤。 */
  status: string;
  /** 伏笔风险筛选（riskByName join 派生）；"all" 不过滤。 */
  risk: string;
  /** 排序：按埋设章 / 按预计回收章。 */
  sortKey: "plant" | "resolve";
}

/**
 * 账本行筛选排序（纯函数）：search 全类型通用；状态/风险/排序仅伏笔 Tab
 * （风险来自 stats.foreshadow_risks 的 join，后端算前端展示，D7 口径）。
 */
export function ledgerRows(
  type: BibleType,
  entities: Record<string, unknown>[],
  query: LedgerQuery,
  riskByName: Map<string, string>,
): Record<string, unknown>[] {
  let rows = entities;
  const needle = query.search.trim().toLowerCase();
  if (needle !== "") {
    rows = rows.filter((e) =>
      Object.values(e).some((v) => String(v).toLowerCase().includes(needle)),
    );
  }
  if (type === "foreshadow") {
    if (query.status !== "all") rows = rows.filter((e) => e["status"] === query.status);
    if (query.risk !== "all") {
      rows = rows.filter((e) => (riskByName.get(String(e["name"])) ?? "") === query.risk);
    }
    const key = query.sortKey === "plant" ? "plant_chapter" : "expected_resolve_chapter";
    rows = [...rows].sort((a, b) => chapterSortKey(a[key]) - chapterSortKey(b[key]));
  }
  return rows;
}

/**
 * 风险单元格：风险 chip + 章节进度条。
 * 进度 = 当前最新章节 / 预计回收章（超过 100% 即逾期）；统计未就绪或
 * 预计回收不可解析时进度显示「—」（合法空结果，不报错）。
 */
export function RiskCell({
  risk,
  latest,
  expected,
}: {
  risk: string;
  latest: number;
  expected: string;
}) {
  const meta = RISK_META[risk] ?? { label: "—", chip: "border-line text-paper/50" };
  const expectedNum = parseChapterNumber(expected);
  const hasProgress = latest > 0 && expectedNum !== null;
  const pct = hasProgress ? Math.min(100, Math.round((latest / expectedNum) * 100)) : 0;
  return (
    <div className="min-w-[4.5rem]">
      <span className={`inline-block border px-1.5 py-px text-[10px] ${meta.chip}`}>
        {meta.label}
      </span>
      <div className="mt-1 flex items-center gap-1">
        <div className="h-1 w-12 bg-line">
          <div
            className={`h-1 ${risk === "overdue" ? "bg-overdue" : "bg-recovered"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-[10px] text-paper/50">
          {hasProgress ? `${latest}/${expectedNum} 章` : "—"}
        </span>
      </div>
    </div>
  );
}
