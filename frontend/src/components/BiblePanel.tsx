/**
 * 故事账本面板（P3 T9，计划 D7/D9；P6 T7 账本底部统计卡）：五账本 Tab 表格 +
 * 伏笔未回收红点 + 全工作区统计（回收率环/状态分布/逾期数）。
 *
 * 数据流：初始走 GET /api/bible/{type} 与 GET /api/{slug}/stats；收到写工具
 * 成功的 ToolExecutionEndEvent 后按 refetchTargets 精准刷新账本与统计。面板是
 * 呈现层——不校验 Bible 内容（数据正确性仍由工具层守门），损坏 YAML 的 422
 * 可读错误就地展示。
 */

import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import type { EventFrame } from "../api/types";
import {
  BIBLE_LABELS,
  BIBLE_TYPES,
  fetchBible,
  fetchStats,
  refetchTargets,
  type BibleType,
  type StoryStatsWire,
} from "../api/rest";
import { useChatStore } from "../state/store";

/** 关系图（T10）懒加载：React Flow + d3-force 不进创作台首屏包。 */
const RelationGraph = lazy(() => import("./RelationGraph"));

interface LedgerState {
  loading: boolean;
  error: string | null;
  entities: Record<string, unknown>[];
}

function initialLedgers(): Record<BibleType, LedgerState> {
  const empty = (): LedgerState => ({ loading: false, error: null, entities: [] });
  return {
    character: empty(),
    clue: empty(),
    foreshadow: empty(),
    method: empty(),
    timeline: empty(),
  };
}

const COLUMNS: Record<BibleType, Array<{ key: string; label: string }>> = {
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

function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join("、") : "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

/** 伏笔生命周期着色：未回收（非 resolved）红点，已回收绿点。 */
function foreshadowDot(status: unknown): string {
  return status === "resolved" ? "bg-planted" : "bg-overdue";
}

/** 伏笔状态分布中文标签与语义色（统计卡 chips）。 */
const FORESHADOW_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  planned: { label: "已计划", color: "text-planned" },
  planted: { label: "已埋设", color: "text-planted" },
  echoed: { label: "已呼应", color: "text-gold-soft" },
  resolved: { label: "已回收", color: "text-recovered" },
};

/** 回收率环：SVG 弧长按 rate 填充（rate=0 空环，rate=1 满环）。 */
function RecoveryRing({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100);
  const r = 14;
  const circumference = 2 * Math.PI * r;
  const filled = Math.min(1, Math.max(0, rate));
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" aria-label={`伏笔回收率 ${pct}%`}>
      <circle
        cx="18"
        cy="18"
        r={r}
        fill="none"
        stroke="currentColor"
        className="text-line"
        strokeWidth="3"
      />
      <circle
        cx="18"
        cy="18"
        r={r}
        fill="none"
        stroke="currentColor"
        className="text-recovered"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray={`${(circumference * filled).toFixed(1)} ${circumference.toFixed(1)}`}
        transform="rotate(-90 18 18)"
      />
      <text x="18" y="21" textAnchor="middle" fontSize="8" className="fill-paper">
        {pct}%
      </text>
    </svg>
  );
}

/** 弧光徽章（T9）：平 / 成长 / 蜕变三阶段，与状态机 TRANSITIONS 同源。 */
const ARC_META: Record<string, { label: string; cls: string }> = {
  flat: { label: "平", cls: "border-line text-paper/50" },
  developing: { label: "成长", cls: "border-gold/60 text-gold" },
  transformed: { label: "蜕变", cls: "border-recovered/60 text-recovered" },
};

/** 角色小传字段（T9 卡片 / T10 关系图联动共用）：动机 / 秘密（折叠）/ 关系 / 备注。 */
function CharacterBio({
  entity,
  secretShown,
  onRevealSecret,
}: {
  entity: Record<string, unknown>;
  secretShown: boolean;
  onRevealSecret: () => void;
}) {
  const relationships = Array.isArray(entity["relationships"])
    ? (entity["relationships"] as string[]).join("；")
    : "";
  return (
    <div className="space-y-1.5 text-xs leading-relaxed text-paper/80">
      {String(entity["motivation"] ?? "") !== "" && (
        <p>
          <span className="text-gold-soft">动机：</span>
          {String(entity["motivation"])}
        </p>
      )}
      {String(entity["secret"] ?? "") !== "" && (
        <div>
          {secretShown ? (
            <p>
              <span className="text-gold-soft">秘密：</span>
              {String(entity["secret"])}
            </p>
          ) : (
            <button
              type="button"
              onClick={onRevealSecret}
              className="border border-line px-1.5 py-px text-[10px] text-paper/60 transition-colors hover:border-gold/60 hover:text-gold"
            >
              查看秘密 ▸
            </button>
          )}
        </div>
      )}
      {relationships !== "" && (
        <p>
          <span className="text-gold-soft">关系：</span>
          {relationships}
        </p>
      )}
      {String(entity["notes"] ?? "") !== "" && (
        <p>
          <span className="text-gold-soft">备注：</span>
          {String(entity["notes"])}
        </p>
      )}
    </div>
  );
}

/** 风险语义色（T8）：逾期红 / 临近暗金 / 正常绿，与账本生命周期色系同源。 */
const RISK_META: Record<string, { label: string; chip: string }> = {
  overdue: { label: "逾期", chip: "border-overdue/60 text-overdue" },
  near: { label: "临近", chip: "border-gold/60 text-gold" },
  normal: { label: "正常", chip: "border-planted/60 text-planted" },
};

/** 从章节 id（ch03）提取章号；非 chNN 形态返回 null（进度条不可判定）。 */
function parseChapterNumber(chapterId: string): number | null {
  const match = /^ch(\d+)$/i.exec(chapterId.trim());
  return match === null ? null : Number(match[1]);
}

/**
 * 风险单元格（T8）：风险 chip + 章节进度条。
 * 进度 = 当前最新章节 / 预计回收章（超过 100% 即逾期）；统计未就绪或
 * 预计回收不可解析时进度显示「—」（合法空结果，不报错）。
 */
function RiskCell({
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

export default function BiblePanel() {
  const [active, setActive] = useState<BibleType>("character");
  const [ledgers, setLedgers] = useState<Record<BibleType, LedgerState>>(initialLedgers);
  const [stats, setStats] = useState<{ loading: boolean; error: string | null; data: StoryStatsWire | null }>({
    loading: false,
    error: null,
    data: null,
  });
  // 伏笔状态筛选（T8）：all = 不过滤；仅伏笔 Tab 渲染筛选器。
  const [fsFilter, setFsFilter] = useState<string>("all");
  // 角色小传（T9）：单开手风琴；秘密默认折叠，点开显式查看——防作者被剧透的仪式感。
  const [openName, setOpenName] = useState<string | null>(null);
  const [secretShown, setSecretShown] = useState(false);
  // 关系图（T10）：角色 Tab 视图切换（列表/关系图）+ 点节点联动右侧小传。
  const [charView, setCharView] = useState<"list" | "graph">("list");
  const [graphSelected, setGraphSelected] = useState<string | null>(null);
  const [graphSecretShown, setGraphSecretShown] = useState(false);
  const slug = useChatStore((s) => s.slug);

  const load = useCallback(async (workspaceSlug: string, type: BibleType) => {
    setLedgers((s) => ({ ...s, [type]: { ...s[type], loading: true, error: null } }));
    try {
      const body = await fetchBible(workspaceSlug, type);
      setLedgers((s) => ({
        ...s,
        [type]: { loading: false, error: null, entities: body.entities },
      }));
    } catch (exc) {
      setLedgers((s) => ({
        ...s,
        [type]: {
          loading: false,
          error: exc instanceof Error ? exc.message : String(exc),
          entities: [],
        },
      }));
    }
  }, []);

  const loadStats = useCallback(async (workspaceSlug: string) => {
    setStats((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fetchStats(workspaceSlug);
      setStats({ loading: false, error: null, data });
    } catch (exc) {
      setStats({
        loading: false,
        error: exc instanceof Error ? exc.message : String(exc),
        data: null,
      });
    }
  }, []);

  // 工作区切换（slug 变更）→ 五账本全部重拉 + 统计：面板数据只属于当前 slug。
  useEffect(() => {
    if (slug === null) return;
    for (const t of BIBLE_TYPES) void load(slug, t);
    void loadStats(slug);
  }, [load, loadStats, slug]);

  // D7 refetch：事件流里写工具成功 → 按映射精准刷新对应账本与统计。
  // end 帧不带 args，先从 start 帧养 toolCallId → args 查表。
  useEffect(() => {
    const argsByCallId = new Map<string, Record<string, unknown>>();
    return useChatStore.subscribe((state, prev) => {
      if (state.eventLog.length <= prev.eventLog.length) return;
      const workspaceSlug = state.slug;
      if (workspaceSlug === null) return;
      const targets = new Set<BibleType>();
      for (const e of state.eventLog.slice(prev.eventLog.length) as EventFrame[]) {
        if (e.type === "tool_execution_start") argsByCallId.set(e.toolCallId, e.args);
        for (const t of refetchTargets(e, (id) => argsByCallId.get(id))) targets.add(t);
      }
      for (const t of targets) void load(workspaceSlug, t);
      if (targets.size > 0) void loadStats(workspaceSlug); // 账本有变动 → 统计重派生
    });
  }, [load, loadStats]);

  const ledger = ledgers[active];
  const columns = COLUMNS[active];
  // T8：风险按名 join stats 派生列表（后端算前端展示，D7）；筛选仅伏笔 Tab。
  const riskByName = new Map(
    (stats.data?.foreshadow_risks ?? []).map((r) => [r.name, r.risk]),
  );
  const rows =
    active === "foreshadow" && fsFilter !== "all"
      ? ledger.entities.filter((e) => e["status"] === fsFilter)
      : ledger.entities;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap gap-1 border-b border-line px-3 py-2">
        {BIBLE_TYPES.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setActive(t)}
            className={`border px-2 py-0.5 text-xs transition-colors ${
              active === t
                ? "border-gold bg-gold/15 text-gold"
                : "border-line text-gold-soft hover:border-gold/50"
            }`}
          >
            {BIBLE_LABELS[t]}
          </button>
        ))}
        {active === "foreshadow" && ledger.entities.length > 0 && (
          <select
            aria-label="伏笔状态筛选"
            value={fsFilter}
            onChange={(e) => setFsFilter(e.target.value)}
            className="ml-auto border border-line bg-transparent px-1.5 py-0.5 text-xs text-paper focus:border-gold/60 focus:outline-none"
          >
            <option value="all" className="bg-ink">全部</option>
            {Object.entries(FORESHADOW_STATUS_LABELS).map(([status, meta]) => (
              <option key={status} value={status} className="bg-ink">
                {meta.label}
              </option>
            ))}
          </select>
        )}
        {active === "character" && ledger.entities.length > 0 && (
          <div className="ml-auto flex gap-1">
            {(["list", "graph"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setCharView(v)}
                className={`border px-2 py-0.5 text-xs transition-colors ${
                  charView === v
                    ? "border-gold bg-gold/15 text-gold"
                    : "border-line text-gold-soft hover:border-gold/50"
                }`}
              >
                {v === "list" ? "列表" : "关系图"}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {ledger.error && (
          <p className="mb-2 border border-overdue/50 bg-overdue/10 px-2 py-1.5 text-xs leading-relaxed text-paper">
            {ledger.error}
          </p>
        )}
        {!ledger.error && ledger.entities.length === 0 && (
          <p className="mt-6 text-center text-xs text-paper/40">
            {ledger.loading ? "读取中…" : "该账本暂无条目（用 bible_upsert 工具登记）。"}
          </p>
        )}
        {!ledger.error && ledger.entities.length > 0 && rows.length === 0 && (
          <p className="mt-6 text-center text-xs text-paper/40">该筛选下暂无条目。</p>
        )}
        {rows.length > 0 &&
          (active === "character" ? (
            charView === "graph" ? (
              <div className="flex min-h-0 flex-col">
                <div className="flex min-h-0 flex-1">
                  <div className="min-h-0 flex-1">
                    <Suspense
                      fallback={
                        <p className="p-4 text-xs text-paper/40">关系图加载中…</p>
                      }
                    >
                      <RelationGraph
                        characters={ledger.entities}
                        selected={graphSelected}
                        onSelect={(name) => setGraphSelected(name)}
                      />
                    </Suspense>
                  </div>
                  {graphSelected !== null && (
                    <aside className="w-56 flex-none overflow-auto border-l border-line px-3 py-2">
                      {(() => {
                        const entity = ledger.entities.find(
                          (e) => String(e["name"]) === graphSelected,
                        );
                        return entity !== undefined ? (
                          <>
                            <div className="mb-2 border-b border-line/60 pb-1 text-sm text-paper">
                              {graphSelected}
                            </div>
                            <CharacterBio
                              entity={entity}
                              secretShown={graphSecretShown}
                              onRevealSecret={() => setGraphSecretShown(true)}
                            />
                          </>
                        ) : null;
                      })()}
                    </aside>
                  )}
                </div>
              </div>
            ) : (
            <div className="space-y-2">
              {rows.map((c) => {
                const name = String(c["name"] ?? "");
                const isOpen = openName === name;
                return (
                  <div key={name} className="border border-line/50 px-2.5 py-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (openName === name) {
                          setOpenName(null);
                        } else {
                          setOpenName(name);
                          setSecretShown(false); // 每次展开重置：秘密需再次显式点开
                        }
                      }}
                      className="w-full text-left"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-paper">{name}</span>
                        <span className="truncate text-[10px] text-paper/50">
                          {String(c["identity"] ?? "")}
                        </span>
                        {(() => {
                          const arc = ARC_META[String(c["arc"] ?? "")];
                          return arc !== undefined ? (
                            <span className={`border px-1 py-px text-[10px] ${arc.cls}`}>
                              {arc.label}
                            </span>
                          ) : null;
                        })()}
                        <span className="ml-auto text-paper/40">{isOpen ? "▾" : "▸"}</span>
                      </div>
                    </button>
                    {isOpen && (
                      <div className="mt-2">
                        <CharacterBio
                          entity={c}
                          secretShown={secretShown}
                          onRevealSecret={() => setSecretShown(true)}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            )
          ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line text-gold-soft">
                {columns.map((c) => (
                  <th key={c.key} className="px-1.5 py-1 font-normal">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((entity, i) => (
                <tr key={i} className="border-b border-line/40 align-top text-paper/80">
                  {columns.map((c) => {
                    // 风险列（T8）：由 stats 派生列表 join 渲染，不走 entity[c.key]。
                    if (active === "foreshadow" && c.key === "risk") {
                      return (
                        <td key={c.key} className="px-1.5 py-1.5">
                          <RiskCell
                            risk={riskByName.get(String(entity["name"])) ?? ""}
                            latest={stats.data?.latest_chapter ?? 0}
                            expected={String(entity["expected_resolve_chapter"] ?? "")}
                          />
                        </td>
                      );
                    }
                    const text = cellText(entity[c.key]);
                    return (
                      <td key={c.key} className="px-1.5 py-1.5">
                        {active === "foreshadow" && c.key === "name" && (
                          <span
                            className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${foreshadowDot(
                              entity["status"],
                            )}`}
                          />
                        )}
                        {/* 长内容截断（D6 布局不变量），悬停 title 看全文；容器 overflow-auto 兜底。 */}
                        <span className="inline-block max-w-[9rem] truncate align-middle" title={text}>
                          {text}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          ))}
      </div>
      {/* 账本底部统计卡（P6 T7，D7）：伏笔回收率环 + 状态分布 + 逾期数 + 章节规模。 */}
      <div className="flex-none border-t border-line px-3 py-2">
        {stats.error ? (
          <p className="text-xs leading-relaxed text-overdue">{stats.error}</p>
        ) : stats.data === null ? (
          <p className="text-xs text-paper/40">
            {stats.loading ? "统计读取中…" : "暂无统计数据"}
          </p>
        ) : (
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <RecoveryRing rate={stats.data.foreshadow_recovery_rate} />
              <div className="leading-tight">
                <div className="text-gold-soft">伏笔回收</div>
                <div className="text-paper/70">
                  {stats.data.foreshadow_resolved}/{stats.data.foreshadow_total}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {Object.entries(stats.data.foreshadow_counts).map(([status, count]) => {
                const meta = FORESHADOW_STATUS_LABELS[status] ?? {
                  label: status,
                  color: "text-paper/50",
                };
                return (
                  <span key={status} className={`${meta.color}`}>
                    {meta.label} {count}
                  </span>
                );
              })}
            </div>
            <div className="ml-auto text-right leading-tight">
              <div className={stats.data.overdue_foreshadows.length > 0 ? "text-overdue" : "text-paper/70"}>
                逾期 {stats.data.overdue_foreshadows.length}
              </div>
              <div className="text-paper/50">
                {stats.data.chapters} 章 · {stats.data.total_chars.toLocaleString("zh-CN")} 字
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
