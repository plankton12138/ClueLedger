/**
 * 账本全页（P7 T5，计划 D5）：从创作台账本面板「展开 ⤢」下钻的全页视图。
 *
 * - 状态独立（不与右栏共享 state）：进入时按当前 slug 重拉五账本 + 统计 +
 *   章节（避免双源同步问题）；呈现层不校验 Bible 内容，422 可读错误就地展示；
 * - 顶部章节进度横条：GET chapters 派生（1..N 章节点，当前选中章高亮），
 *   纯展示不可点（点击章节属正文面板职责，避免双处管理选中态）；
 * - 工具行：搜索框（本地过滤）+ 状态筛选 + 风险筛选 + 排序切换（仅伏笔
 *   Tab）；筛选排序逻辑在 ledgerShared.ledgerRows（纯函数，vitest 钉住）；
 * - 全宽表格：五账本 Tab 可切，默认伏笔；风险列由 stats.foreshadow_risks
 *   join 渲染（后端算前端展示，D7 口径）；
 * - 右栏（lg+）：回收进度大环 + 逾期风险卡（逐条定位 → navigate chapter，
 *   App 现有 nav effect 自动切回创作台正文）+ 角色关系图（切角色 Tab 显示）；
 * - <lg：右栏内容折叠进表格下方（全页空间足够，不做第二套抽屉）。
 */

import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import {
  BIBLE_LABELS,
  BIBLE_TYPES,
  fetchBible,
  fetchChapters,
  fetchStats,
  type BibleType,
  type StoryStatsWire,
} from "../api/rest";
import { useChatStore } from "../state/store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import RingStat from "./RingStat";
import {
  COLUMNS,
  FORESHADOW_STATUS_LABELS,
  RISK_META,
  RiskCell,
  cellText,
  foreshadowDot,
  ledgerRows,
  parseChapterNumber,
} from "./ledgerShared";

/** 关系图懒加载：与创作台同策略，React Flow + d3-force 不进首屏包。 */
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

export default function LedgerPage({ onBack }: { onBack: () => void }) {
  const slug = useChatStore((s) => s.slug);
  const currentChapter = useChatStore((s) => s.manuscript.chapterId);
  const navigate = useChatStore((s) => s.navigate);
  const [active, setActive] = useState<BibleType>("foreshadow");
  const [ledgers, setLedgers] = useState<Record<BibleType, LedgerState>>(initialLedgers);
  const [stats, setStats] = useState<{ loading: boolean; error: string | null; data: StoryStatsWire | null }>({
    loading: false,
    error: null,
    data: null,
  });
  const [chapters, setChapters] = useState<string[]>([]);
  // 工具行（D5）：搜索 / 状态 / 风险 / 排序；状态/风险/排序仅伏笔 Tab 渲染。
  const [search, setSearch] = useState("");
  const [fsFilter, setFsFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sortKey, setSortKey] = useState<"plant" | "resolve">("plant");
  // 关系图节点联动小传选中。
  const [graphSelected, setGraphSelected] = useState<string | null>(null);

  const load = useCallback(async (workspaceSlug: string, type: BibleType) => {
    setLedgers((s) => ({ ...s, [type]: { ...s[type], loading: true, error: null } }));
    try {
      const body = await fetchBible(workspaceSlug, type);
      setLedgers((s) => ({ ...s, [type]: { loading: false, error: null, entities: body.entities } }));
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

  // 进入全页 / 换工作区：五账本 + 统计 + 章节全部重拉（状态独立，D5）。
  useEffect(() => {
    if (slug === null) return;
    for (const t of BIBLE_TYPES) void load(slug, t);
    setStats((s) => ({ ...s, loading: true, error: null }));
    fetchStats(slug)
      .then((data) => setStats({ loading: false, error: null, data }))
      .catch((exc: unknown) =>
        setStats({
          loading: false,
          error: exc instanceof Error ? exc.message : String(exc),
          data: null,
        }),
      );
    fetchChapters(slug)
      .then(setChapters)
      .catch(() => setChapters([])); // 进度条是纯展示：拉不到不挡账本（合法降级）
  }, [load, slug]);

  const ledger = ledgers[active];
  const riskByName = new Map((stats.data?.foreshadow_risks ?? []).map((r) => [r.name, r.risk]));
  const rows = ledgerRows(
    active,
    ledger.entities,
    { search, status: fsFilter, risk: riskFilter, sortKey },
    riskByName,
  );
  const columns = COLUMNS[active];

  return (
    <main className="flex min-h-0 flex-1 flex-col border-t border-line">
      {/* 页头：标题 + 返回创作台（下钻页不入主导航，D1）。 */}
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <h2 className="font-serif text-sm font-bold text-gold">故事账本</h2>
        <span className="min-w-0 truncate text-xs text-paper/45">{slug ?? "—"}</span>
        <Button
          variant="outline"
          onClick={onBack}
          className="ml-auto h-auto px-2 py-0.5 text-xs text-paper/70 hover:border-gold/50 hover:bg-transparent hover:text-gold"
        >
          ‹ 返回创作台
        </Button>
      </div>
      {/* 章节进度横条：纯展示不可点，当前选中章高亮（D5）。 */}
      <div className="flex flex-none flex-wrap items-center gap-1.5 border-b border-line px-4 py-1.5 text-xs">
        <span className="text-gold-soft">章节进度</span>
        {chapters.length === 0 && <span className="text-paper/35">暂无章节</span>}
        {chapters.map((id) => {
          const n = parseChapterNumber(id);
          const isCurrent = id === currentChapter;
          return (
            <span
              key={id}
              title={id}
              className={`border px-1.5 py-px font-mono text-[10px] ${
                isCurrent ? "border-gold bg-gold/15 text-gold" : "border-line text-paper/50"
              }`}
            >
              {n === null ? id : `第${n}章`}
            </span>
          );
        })}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
        {/* 主区：工具行 + 全宽表格。 */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-4 py-2">
            {BIBLE_TYPES.map((t) => (
              <Button
                key={t}
                variant="outline"
                onClick={() => setActive(t)}
                className={`h-auto px-2 py-0.5 text-xs ${
                  active === t
                    ? "border-gold bg-gold/15 text-gold hover:bg-gold/15 hover:text-gold"
                    : "text-gold-soft hover:border-gold/50 hover:bg-transparent"
                }`}
              >
                {BIBLE_LABELS[t]}
              </Button>
            ))}
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="本地过滤…"
              aria-label="账本条目过滤"
              className="ml-auto w-36 py-0.5"
            />
            {active === "foreshadow" && (
              <>
                <Select value={fsFilter} onValueChange={setFsFilter}>
                  <SelectTrigger aria-label="伏笔状态筛选" className="px-1.5 py-0.5 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    {Object.entries(FORESHADOW_STATUS_LABELS).map(([status, meta]) => (
                      <SelectItem key={status} value={status}>
                        {meta.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={riskFilter} onValueChange={setRiskFilter}>
                  <SelectTrigger aria-label="伏笔风险筛选" className="px-1.5 py-0.5 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部风险</SelectItem>
                    {Object.entries(RISK_META).map(([risk, meta]) => (
                      <SelectItem key={risk} value={risk}>
                        {meta.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="outline"
                  onClick={() => setSortKey((k) => (k === "plant" ? "resolve" : "plant"))}
                  title="切换排序键"
                  className="h-auto px-2 py-0.5 text-xs text-paper/70 hover:border-gold/50 hover:bg-transparent hover:text-gold"
                >
                  排序：{sortKey === "plant" ? "埋设章" : "预计回收章"}
                </Button>
              </>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-4 py-2">
            {ledger.error && (
              <p className="mb-2 border border-overdue/50 bg-overdue/10 px-2 py-1.5 text-xs leading-relaxed text-paper">
                {ledger.error}
              </p>
            )}
            {!ledger.error && ledger.entities.length === 0 && (
              <p className="mt-6 text-center text-xs text-paper/40">
                {ledger.loading ? "读取中…" : "该账本暂无条目。"}
              </p>
            )}
            {!ledger.error && ledger.entities.length > 0 && rows.length === 0 && (
              <p className="mt-6 text-center text-xs text-paper/40">该筛选下暂无条目。</p>
            )}
            {rows.length > 0 && (
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-line text-gold-soft">
                    <th className="w-8 whitespace-nowrap px-1.5 py-1 font-normal">#</th>
                    {columns.map((c) => (
                      <th key={c.key} className="whitespace-nowrap px-1.5 py-1 font-normal">
                        {c.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((entity, i) => {
                    const name = String(entity["name"] ?? "");
                    return (
                      <tr key={i} className="border-b border-line/40 align-top text-paper/80">
                        <td className="px-1.5 py-1.5 font-mono text-paper/35">{i + 1}</td>
                        {columns.map((c) => {
                          if (active === "foreshadow" && c.key === "risk") {
                            return (
                              <td key={c.key} className="px-1.5 py-1.5">
                                <RiskCell
                                  risk={riskByName.get(name) ?? ""}
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
                              {active === "foreshadow" && c.key === "status" ? (
                                (() => {
                                  const meta =
                                    FORESHADOW_STATUS_LABELS[String(entity["status"])] ?? {
                                      label: text,
                                      color: "text-paper/60",
                                    };
                                  return (
                                    <span className={`inline-block max-w-[9rem] truncate align-middle ${meta.color}`} title={text}>
                                      {meta.label}
                                    </span>
                                  );
                                })()
                              ) : (
                                <span className="inline-block max-w-[9rem] truncate align-middle" title={text}>
                                  {text}
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>
        {/* 右栏（lg+ 侧贴，<lg 折叠进表格下方）：回收大环 + 逾期风险卡 + 关系图。 */}
        <aside className="w-full flex-none space-y-3 border-t border-line px-4 py-3 lg:w-80 lg:border-l lg:border-t-0">
          <div>
            <h3 className="mb-2 font-serif text-sm font-bold text-gold">回收进度</h3>
            {stats.error !== null ? (
              <p className="text-xs leading-relaxed text-overdue">{stats.error}</p>
            ) : stats.data === null ? (
              <p className="text-xs text-paper/40">{stats.loading ? "统计读取中…" : "暂无统计数据"}</p>
            ) : (
              <div className="flex items-center gap-3">
                <RingStat rate={stats.data.foreshadow_recovery_rate} size={64} label="伏笔回收率" />
                <div className="min-w-0 text-xs leading-relaxed">
                  <div className="text-paper/70">
                    已回收 {stats.data.foreshadow_resolved}/{stats.data.foreshadow_total}
                  </div>
                  <div className="text-paper/45">
                    {stats.data.chapters} 章 · {stats.data.total_chars.toLocaleString("zh-CN")} 字
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="border-t border-line pt-3">
            <h3 className="mb-2 font-serif text-sm font-bold text-gold">逾期风险</h3>
            {stats.data === null || stats.data.overdue_foreshadows.length === 0 ? (
              <p className="text-xs text-planted/80">
                {stats.data === null ? "—" : "✓ 无逾期伏笔"}
              </p>
            ) : (
              <ul className="space-y-1.5 text-xs">
                {stats.data.overdue_foreshadows.map((f) => {
                  const anchor =
                    parseChapterNumber(f.expected_resolve_chapter) !== null
                      ? f.expected_resolve_chapter
                      : f.plant_chapter;
                  return (
                    <li key={f.name} className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-paper/80" title={f.name}>
                        {f.name}
                      </span>
                      <span className="whitespace-nowrap text-[10px] text-paper/45">
                        应于 {f.expected_resolve_chapter || "—"} 回收
                      </span>
                      <Button
                        variant="outline"
                        onClick={() => navigate({ kind: "chapter", chapterId: anchor })}
                        className="h-auto shrink-0 px-1.5 py-px text-[10px] text-paper/60 hover:border-gold/60 hover:bg-transparent hover:text-gold"
                        title={`定位到 ${anchor}（回到创作台正文）`}
                      >
                        定位
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          {active === "character" && (
            <div className="border-t border-line pt-3">
              <h3 className="mb-2 font-serif text-sm font-bold text-gold">角色关系图</h3>
              <div className="h-72 border border-line/50">
                <Suspense fallback={<p className="p-4 text-xs text-paper/40">关系图加载中…</p>}>
                  <RelationGraph
                    characters={ledger.entities}
                    selected={graphSelected}
                    onSelect={(name) => setGraphSelected(name)}
                  />
                </Suspense>
              </div>
              {graphSelected !== null && (
                <p className="mt-2 text-xs text-paper/50">已选中：{graphSelected}</p>
              )}
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
