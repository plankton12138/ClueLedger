/**
 * 事件流页（P6 T5，计划 D5；P7 T4 对齐计划 D8）：events.jsonl 历史（REST）
 * + WS 实时帧的账本镜像。
 *
 * - 左主区：时间 / 类型徽章 / 内容摘要，点击行展开完整 JSON。徽章五类配色：
 *   消息开始/结束=蓝、工具执行=绿、工具返回=紫（violet）、审计/notice=金、
 *   错误/契约外=红（可观测性是事件契约的一部分——未知事件显式上报，不静默吞）；
 * - 右侧栏「运行概览」：会话状态环（连接/运行二态换色）+ 四指标卡
 *   （事件总数/工具调用/平均延迟/错误数）；平均延迟 = 可见行内
 *   tool_execution_start→end 帧对均值（api/events.ts avgToolLatencyMs 纯函数）；
 * - 合并边界：发起 fetch 前记录窗口下沿、响应落地时定格上沿；窗口内帧按
 *   指纹去重（可能与历史重叠），窗口后帧无条件追加（必然晚于服务端读盘）。
 *   规则实现在 api/events.ts mergeEventRows（纯函数，vitest 钉住）。
 * - 流式中间态 message_update 不显示：账本不收它（T4 决策），镜像同口径。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  avgToolLatencyMs,
  categorize,
  formatLatencyMs,
  formatTs,
  indexedEvent,
  indexedNotice,
  isKnownEvent,
  mergeEventRows,
  summarize,
  type EventRecordWire,
} from "../api/events";
import { fetchEvents } from "../api/rest";
import type { ConnectionStatus } from "../api/socket";
import { useChatStore } from "../state/store";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import RingStat from "./RingStat";

const PAGE_SIZE = 200;

type FilterCategory = "all" | "message" | "tool" | "error";

const FILTERS: readonly { key: FilterCategory; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "message", label: "消息" },
  { key: "tool", label: "工具" },
  { key: "error", label: "错误" },
];

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "连接中",
  open: "已连接",
  closed: "已断开",
};

/** 状态环三态：运行绿满环 / 已连接蓝满环 / 连接中金半环 / 断开红空环。 */
const STATUS_RING: Record<ConnectionStatus, { rate: number; color: string }> = {
  connecting: { rate: 0.5, color: "text-gold-soft" },
  open: { rate: 1, color: "text-planned" },
  closed: { rate: 0, color: "text-overdue" },
};

/** 徽章五类配色（计划 D8）：按事件类型定位，isError 与契约外压到红色。 */
function categoryBadge(type: string, frame: Record<string, unknown>): string {
  if (!isKnownEvent(type)) return "border-overdue bg-overdue/10 text-overdue";
  if (categorize(type, frame) === "error") return "border-overdue/60 text-overdue";
  if (type === "notice") return "border-gold/60 text-gold";
  if (type === "message_start" || type === "message_end") return "border-planned/60 text-planned";
  if (type === "tool_execution_end") return "border-violet/60 text-violet";
  if (type.startsWith("tool_execution")) return "border-planted/60 text-planted";
  return "border-line text-gold-soft";
}

export default function EventStreamView() {
  const slug = useChatStore((s) => s.slug);
  const eventLog = useChatStore((s) => s.eventLog);
  const noticeLog = useChatStore((s) => s.noticeLog);
  const unhandled = useChatStore((s) => s.unhandledEventTypes);
  const running = useChatStore((s) => s.running);
  const status = useChatStore((s) => s.status);
  const snapshot = useChatStore((s) => s.snapshot);

  const [records, setRecords] = useState<EventRecordWire[]>([]); // 升序（旧→新）
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState<FilterCategory>("all");
  const [autoScroll, setAutoScroll] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  // 合并窗口（绝对序号）：[start, end) = 取数窗口（帧可能已在响应里，按指纹
  // 去重），[end, ∞) = 响应后到达（无条件追加），[0, start) = 交给历史。
  // start 只在整页重置（换工作区/切分类）时移动；end 随每次响应单调推进，
  // 「加载更早」不动它——否则两次取数之间到达的帧会被误藏。
  const winStartEvent = useRef(0);
  const winStartNotice = useRef(0);
  const winEndEvent = useRef(Number.MAX_SAFE_INTEGER);
  const winEndNotice = useRef(Number.MAX_SAFE_INTEGER);
  const requestTicket = useRef(0);
  const recordsRef = useRef<EventRecordWire[]>([]);
  recordsRef.current = records;

  const load = useCallback(
    async (mode: "reset" | "older") => {
      if (slug === null) return;
      const ticket = ++requestTicket.current;
      const startState = useChatStore.getState();
      if (mode === "reset") {
        winStartEvent.current = startState.eventLog.length;
        winStartNotice.current = startState.noticeLog.length;
        // 响应落地前不产实时尾（帧暂按窗口内去重候选处理，落地后自校正）
        winEndEvent.current = Number.MAX_SAFE_INTEGER;
        winEndNotice.current = Number.MAX_SAFE_INTEGER;
      }
      setLoading(true);
      if (mode === "reset") setError(null);
      try {
        const page = await fetchEvents(slug, {
          limit: PAGE_SIZE,
          beforeSeq: mode === "older" ? recordsRef.current[0]?.seq : undefined,
          category: filter === "all" ? undefined : filter,
        });
        if (ticket !== requestTicket.current) return; // 晚到的旧响应丢弃（切分类/换区竞态）
        winEndEvent.current = useChatStore.getState().eventLog.length;
        winEndNotice.current = useChatStore.getState().noticeLog.length;
        const ascending = [...page.events].reverse();
        setRecords((prev) => (mode === "older" ? [...ascending, ...prev] : ascending));
        setHasMore(page.has_more);
        setTotal(page.total);
        setCounts(page.counts);
      } catch (exc) {
        if (ticket === requestTicket.current) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      } finally {
        if (ticket === requestTicket.current) setLoading(false);
      }
    },
    [slug, filter],
  );

  // 整页重置的触发点：换工作区 / 切分类（load 身份变化即重取）
  useEffect(() => {
    setRecords([]);
    setHasMore(false);
    void load("reset");
  }, [load]);

  const rows = useMemo(() => {
    const inflightEvents = eventLog
      .slice(winStartEvent.current, winEndEvent.current)
      .map((frame, i) => indexedEvent(winStartEvent.current + i, frame));
    const liveEvents = eventLog
      .slice(winEndEvent.current)
      .map((frame, i) => indexedEvent(winEndEvent.current + i, frame));
    const inflightNotices = noticeLog
      .slice(winStartNotice.current, winEndNotice.current)
      .map((notice, i) => indexedNotice(winStartNotice.current + i, notice));
    const liveNotices = noticeLog
      .slice(winEndNotice.current)
      .map((notice, i) => indexedNotice(winEndNotice.current + i, notice));
    const merged = mergeEventRows(
      records,
      [...inflightEvents, ...inflightNotices],
      [...liveEvents, ...liveNotices],
    );
    return filter === "all" ? merged : merged.filter((row) => categorize(row.type, row.frame) === filter);
    // 窗口 ref 的读值变化总伴随 setRecords/setState 触发重算，无需列入依赖
  }, [records, eventLog, noticeLog, filter]);

  // 平均延迟按可见行派生（随过滤联动）：实时行无服务端时间戳不参与，
  // 刷新后历史行补齐（计划 D8 的账本镜像口径）。
  const latency = useMemo(() => avgToolLatencyMs(rows), [rows]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [rows.length, autoScroll]);

  const countOf = (key: FilterCategory): number =>
    key === "all" ? total : (counts[key] ?? 0);

  return (
    <div className="flex h-full min-h-0">
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-none flex-wrap items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
          <span className="font-serif text-sm font-bold text-gold">事件流</span>
          <span className="text-xs text-paper/45">账本共 {total} 帧</span>
          <nav className="flex gap-1">
            {FILTERS.map((item) => (
              <Button
                key={item.key}
                variant="outline"
                onClick={() => setFilter(item.key)}
                className={`h-auto px-2 py-0.5 text-xs ${
                  filter === item.key
                    ? "border-gold/60 bg-gold/10 text-gold hover:bg-gold/10 hover:text-gold"
                    : "border-transparent text-paper/60 hover:border-transparent hover:bg-transparent hover:text-gold"
                }`}
              >
                {item.label}
                <small className="ml-1 text-paper/40">{countOf(item.key)}</small>
              </Button>
            ))}
          </nav>
          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs text-paper/60">
            <Checkbox
              checked={autoScroll}
              onCheckedChange={(v) => setAutoScroll(v === true)}
              aria-label="自动滚动"
            />
            自动滚动
          </label>
          {slug !== null && (
            <a
              href={`/api/${encodeURIComponent(slug)}/events/export`}
              download
              className="border border-line px-2 py-0.5 text-xs text-paper/70 transition-colors hover:border-gold/50 hover:text-gold"
            >
              导出账本
            </a>
          )}
          <Button
            variant="outline"
            onClick={() => void load("older")}
            disabled={!hasMore || loading}
            className="h-auto px-2 py-0.5 text-xs text-paper/70 hover:border-gold/50 hover:bg-transparent hover:text-gold disabled:opacity-30"
          >
            加载更早
          </Button>
        </div>
        {unhandled.length > 0 && (
          <div className="border-b border-overdue/50 bg-overdue/10 px-4 py-1.5 text-xs leading-relaxed text-overdue">
            契约外事件类型（显式上报）：{unhandled.join("、")}
          </div>
        )}
        {error && (
          <div className="border-b border-overdue/40 bg-overdue/10 px-4 py-2 text-xs leading-relaxed text-overdue">
            {error}
            <Button
              variant="outline"
              onClick={() => void load("reset")}
              className="ml-2 h-auto border-overdue/60 px-1.5 py-0.5 text-overdue hover:bg-overdue/10 hover:text-overdue"
            >
              重试
            </Button>
          </div>
        )}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-1 font-mono text-xs">
          {rows.length === 0 && !loading && (
            <p className="mt-6 text-center text-paper/30">
              账本空空如也——运行一次对话/写作流后，事件会同时进入实时流与持久账本。
            </p>
          )}
          {rows.map((row) => {
            const isOpen = expanded === row.key;
            return (
              <div key={row.key} className="border-b border-line/30 py-1">
                <Button
                  variant="ghost"
                  onClick={() => setExpanded(isOpen ? null : row.key)}
                  className="h-auto w-full justify-start rounded-none px-0 py-0 text-left hover:bg-transparent"
                >
                  <span className="w-16 shrink-0 text-paper/30">
                    {row.ts === null ? "实时" : formatTs(row.ts)}
                  </span>
                  <span className={`shrink-0 border px-1 ${categoryBadge(row.type, row.frame)}`}>
                    {row.type}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-paper/50">
                    {summarize(row.type, row.frame)}
                  </span>
                </Button>
                {isOpen && (
                  <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all border border-line bg-ink-soft p-2 text-paper/70">
                    {JSON.stringify(row.frame, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </section>
      <aside className="w-60 flex-none overflow-hidden border-l border-line bg-ink-soft/35 px-4 py-3 text-xs leading-relaxed">
        <h3 className="mb-2 font-serif text-sm font-bold text-gold">运行概览</h3>
        <div className="flex items-center gap-3">
          <RingStat
            rate={running && status === "open" ? 1 : STATUS_RING[status].rate}
            size={48}
            label="会话状态"
            color={running && status === "open" ? "text-planted" : STATUS_RING[status].color}
            center={running && status === "open" ? "运行中" : STATUS_LABEL[status]}
          />
          <div className="min-w-0">
            <div className="text-paper/50">会话状态</div>
            <div className="whitespace-nowrap text-paper">
              {running && status === "open" ? "写作流运行中" : STATUS_LABEL[status]}
            </div>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <MetricCard label="事件总数" value={total} />
          <MetricCard label="工具调用" value={counts["tool"] ?? 0} />
          <MetricCard label="平均延迟" value={latency === null ? "—" : formatLatencyMs(latency)} />
          <MetricCard
            label="错误数"
            value={counts["error"] ?? 0}
            tone={(counts["error"] ?? 0) > 0 ? "text-overdue" : "text-paper"}
          />
        </div>
        <div className="mt-2 flex justify-between gap-2 border-t border-line pt-2">
          <span className="min-w-0 shrink-0 text-paper/50">当前模型</span>
          <span className="min-w-0 truncate font-mono text-paper" title={snapshot?.model}>
            {snapshot?.model ?? "—"}
          </span>
        </div>
        <p className="mt-3 border-t border-line pt-2 text-[10px] leading-relaxed text-paper/35">
          历史来自 events.jsonl（重启不丢）；实时帧在取数窗口后无缝追加。
          「加载更早」按 seq 游标向上翻页。平均延迟按可见行的历史帧对计算，
          实时帧刷新后补齐。
        </p>
      </aside>
    </div>
  );
}

/** 四指标卡（P7 T4 计划 D8）：标签 + 等宽值，压缩时 truncate 兜底。 */
function MetricCard({ label, value, tone = "text-paper" }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="min-w-0 border border-line bg-ink-soft/60 px-2 py-1.5">
      <div className="truncate whitespace-nowrap text-[10px] text-paper/40">{label}</div>
      <div className={`truncate whitespace-nowrap font-mono text-sm ${tone}`}>{value}</div>
    </div>
  );
}
