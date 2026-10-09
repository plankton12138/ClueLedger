/**
 * 故事账本面板（P3 T9，计划 D7/D9；P6 T7 账本底部统计卡）：五账本 Tab 表格 +
 * 伏笔未回收红点 + 全工作区统计（回收率环/状态分布/逾期数）。
 *
 * 数据流：初始走 GET /api/bible/{type} 与 GET /api/{slug}/stats；收到写工具
 * 成功的 ToolExecutionEndEvent 后按 refetchTargets 精准刷新账本与统计。面板是
 * 呈现层——不校验 Bible 内容（数据正确性仍由工具层守门），损坏 YAML 的 422
 * 可读错误就地展示。
 */

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
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
import { clueFairness } from "../state/clueFairness";
import RingStat from "./RingStat";
import {
  COLUMNS,
  FORESHADOW_STATUS_LABELS,
  RiskCell,
  cellText,
  foreshadowDot,
} from "./ledgerShared";

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

/** 回收率环已提取为共享 RingStat（P7 T2，D3）：本组件消费默认形态（36px 回收绿）。 */

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

/** 风险语义色与 RiskCell、伏笔状态标签、章节号解析已提取至 ledgerShared（P7 T5，D5）。 */

export default function BiblePanel({ onExpand }: { onExpand?: () => void }) {
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
  // 全局搜索跳转（P6 T11）：命中行短暂高亮（约 2.4s 后自动熄灭，不留残影）。
  const nav = useChatStore((s) => s.nav);
  const clearNav = useChatStore((s) => s.clearNav);
  const [highlightName, setHighlightName] = useState<string | null>(null);
  const highlightTimer = useRef<number | null>(null);

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

  // 全局搜索跳转（P6 T11）：账本命中 → 切对应 Tab + 高亮目标行（自动熄灭）。
  // 角色命中额外切回列表视图（关系图视图无行可高亮）。消费完即 clearNav。
  useEffect(() => {
    if (nav === null || nav.kind !== "bible") return;
    setActive(nav.type);
    if (nav.type === "character") setCharView("list");
    setHighlightName(nav.name);
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlightName(null), 2400);
    clearNav();
  }, [nav, clearNav]);

  const ledger = ledgers[active];
  const columns = COLUMNS[active];
  // 线索公平环（P7 T3，D4）：与审计中心（T8，D7）同口径——非红鲱鱼已呈现占比；
  // 数据来自面板已加载的线索账本实体，零新端点。空账本/未加载 → null（显示「—」）。
  const fairness = clueFairness(ledgers.clue.entities);
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
      {/* 全页入口（P7 T5，D5）：标题栏「展开 ⤢」下钻账本全页（LedgerPage）。 */}
      {onExpand !== undefined && (
        <div className="flex flex-none items-center justify-between border-b border-line px-3 py-1.5">
          <span className="text-[10px] tracking-[0.2em] text-gold-soft/70">故事账本</span>
          <button
            type="button"
            onClick={onExpand}
            title="展开为全页视图"
            className="border border-line px-1.5 py-0.5 text-[10px] text-paper/60 transition-colors hover:border-gold/60 hover:text-gold"
          >
            ⤢ 展开
          </button>
        </div>
      )}
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
                  <div className="min-h-0 min-w-0 flex-1">
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
                    <aside className="w-56 max-w-[65%] flex-none overflow-auto border-l border-line px-3 py-2">
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
                  <div
                    key={name}
                    className={`border px-2.5 py-2 ${
                      highlightName === name ? "border-gold bg-gold/10" : "border-line/50"
                    }`}
                  >
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
                      <div className="flex min-w-0 items-center gap-2">
                        {/* 人名禁逐字换行（P7 拖拽审查）：overflow-hidden 使 min-width 归零，
                            收缩时省略号收场而非中文竖排；身份列 flex-1 吃剩余宽度。 */}
                        <span className="min-w-0 truncate text-paper">{name}</span>
                        <span className="min-w-0 flex-1 truncate text-[10px] text-paper/50">
                          {String(c["identity"] ?? "")}
                        </span>
                        {(() => {
                          const arc = ARC_META[String(c["arc"] ?? "")];
                          return arc !== undefined ? (
                            <span className={`shrink-0 border px-1 py-px text-[10px] ${arc.cls}`}>
                              {arc.label}
                            </span>
                          ) : null;
                        })()}
                        <span className="ml-auto shrink-0 text-paper/40">{isOpen ? "▾" : "▸"}</span>
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
                  <th key={c.key} className="whitespace-nowrap px-1.5 py-1 font-normal">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((entity, i) => (
                <tr
                  key={i}
                  className={`border-b border-line/40 align-top ${
                    highlightName === String(entity["name"]) ? "bg-gold/10" : "text-paper/80"
                  }`}
                >
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
      {/* 账本底部概览卡（P6 T7 → P7 T3 升级）：伏笔回收环 + 线索公平环 + 状态分布 + 逾期风险行。 */}
      <div className="flex-none border-t border-line px-3 py-2">
        {stats.error ? (
          <p className="text-xs leading-relaxed text-overdue">{stats.error}</p>
        ) : stats.data === null ? (
          <p className="text-xs text-paper/40">
            {stats.loading ? "统计读取中…" : "暂无统计数据"}
          </p>
        ) : (
          <div className="space-y-1.5 text-xs">
            {/* 环块 shrink-0（P7 拖拽审查）：文字说明禁逐字换行；放不下整行换行而非竖排。 */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <div className="flex shrink-0 items-center gap-1.5">
                <RingStat rate={stats.data.foreshadow_recovery_rate} label="伏笔回收率" />
                <div className="whitespace-nowrap leading-tight">
                  <div className="text-gold-soft">伏笔回收</div>
                  <div className="text-paper/70">
                    {stats.data.foreshadow_resolved}/{stats.data.foreshadow_total}
                  </div>
                </div>
              </div>
              {fairness === null ? (
                <div className="shrink-0 whitespace-nowrap leading-tight" title="线索账本暂空或未加载">
                  <div className="text-gold-soft/60">线索公平</div>
                  <div className="text-paper/40">—</div>
                </div>
              ) : (
                <div
                  className="flex shrink-0 items-center gap-1.5"
                  title="非红鲱鱼线索中已呈现占比（与审计中心同口径）"
                >
                  <RingStat rate={fairness.rate} label="线索公平性" color="text-gold" />
                  <div className="whitespace-nowrap leading-tight">
                    <div className="text-gold-soft">线索公平</div>
                    <div className="text-paper/70">
                      {fairness.presented}/{fairness.total}
                    </div>
                  </div>
                </div>
              )}
              <div className="ml-auto whitespace-nowrap text-right leading-tight text-paper/50">
                {stats.data.chapters} 章 · {stats.data.total_chars.toLocaleString("zh-CN")} 字
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
            {stats.data.overdue_foreshadows.length > 0 ? (
              <p
                className="truncate text-overdue"
                title={stats.data.overdue_foreshadows.map((f) => f.name).join("、")}
              >
                ⚠ 逾期 {stats.data.overdue_foreshadows.length}：
                {stats.data.overdue_foreshadows.map((f) => f.name).join("、")}
              </p>
            ) : (
              <p className="text-planted/80">✓ 无逾期伏笔</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
