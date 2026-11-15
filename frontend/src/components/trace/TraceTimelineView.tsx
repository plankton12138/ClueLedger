import { useMemo, useState, type ReactNode } from "react";

import { Button } from "../ui/Button";
import { duration, kinds, statusLabel } from "./labels";
import type { TraceEvent, TraceMark, TraceMissingRow, TurnTrace } from "./types";

const markColor: Record<TraceMark["verdict"], string> = {
  pass: "#1f9d6b",
  fail: "#e0464e",
  skipped: "#9aa1b2",
  pending_review: "#d98a12",
};

export function TraceTimelineView({
  trace,
  error,
  loading,
  marks = [],
  missing = [],
  activeSpanId,
  onSelect,
  onRetry,
  detail,
}: {
  trace?: TurnTrace;
  error?: string;
  loading?: boolean;
  marks?: TraceMark[];
  missing?: TraceMissingRow[];
  activeSpanId?: string;
  onSelect?: (spanId: string) => void;
  onRetry?: () => void;
  detail?: ReactNode;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [localActive, setLocalActive] = useState("");
  const active = activeSpanId ?? localActive;

  const { events, loops, visible, range } = useMemo(() => {
    const list = trace?.events ?? [];
    const loopIds = new Set(list.filter(item => item.kind === "loop").map(item => item.span_id));
    const childMap = new Map<string, TraceEvent[]>();
    for (const item of list) {
      if (!item.parent_span_id || !loopIds.has(item.parent_span_id)) continue;
      const group = childMap.get(item.parent_span_id) ?? [];
      group.push(item);
      childMap.set(item.parent_span_id, group);
    }
    const rootItems = list.filter(
      item => !item.parent_span_id || !loopIds.has(item.parent_span_id),
    );
    const shown = rootItems.flatMap(item =>
      open.has(item.span_id) ? [item, ...(childMap.get(item.span_id) ?? [])] : [item],
    );
    return {
      events: list,
      loops: loopIds,
      visible: shown,
      range: trace?.range_ms || 1,
    };
  }, [trace, open]);

  function select(spanId: string) {
    setLocalActive(spanId);
    onSelect?.(spanId);
  }

  function toggle(item: TraceEvent) {
    select(item.span_id);
    if (item.kind !== "loop") return;
    setOpen(current => {
      const next = new Set(current);
      if (next.has(item.span_id)) next.delete(item.span_id);
      else next.add(item.span_id);
      return next;
    });
  }

  const marksBySpan = useMemo(() => {
    const map = new Map<string, TraceMark[]>();
    for (const mark of marks) {
      const list = map.get(mark.span_id) ?? [];
      list.push(mark);
      map.set(mark.span_id, list);
    }
    return map;
  }, [marks]);

  return (
    <section className="trace-timeline" aria-label="执行时间线">
      {error && (
        <div role="alert">
          {error}
          {onRetry && <Button onClick={onRetry}>重新连接</Button>}
        </div>
      )}
      {loading && !trace && !error && <p role="status">正在读取运行追踪…</p>}
      {trace && (
        <>
          <h3>{trace.turn.text || "无请求文本"}</h3>
          <div className="trace-stats">
            <span>{statusLabel(trace.turn.status)}</span>
            <span>
              总耗时 <strong>{duration(trace.turn.duration_ms)}</strong>
            </span>
            <span>{loops.size} Loops</span>
            <span>{events.filter(item => item.kind === "tool_call").length} 次工具调用</span>
            <span>
              Tokens {trace.usage.input ?? "—"} 入 / {trace.usage.output ?? "—"} 出
              {trace.usage.partial
                ? "（部分记录）"
                : trace.usage.input === null && trace.usage.output === null
                  ? "（未记录）"
                  : ""}
            </span>
            <span>
              作品 {trace.turn.work_commit ? trace.turn.work_commit.slice(0, 7) : "—"}
              {trace.turn.story_revision ? ` · 方案 ${trace.turn.story_revision}` : ""}
            </span>
          </div>
          {!trace.turn.trace_available ? (
            <p>该轮未记录运行追踪，可在「事件流」查看已有记录。</p>
          ) : events.length === 0 && missing.length === 0 ? (
            <p>该轮暂无执行事件。</p>
          ) : (
            <>
              <div className="trace-toolbar">
                <strong>执行时间线</strong>
                <Button
                  disabled={!loops.size || open.size === loops.size}
                  onClick={() => setOpen(loops)}
                >
                  展开全部
                </Button>
                <Button
                  disabled={!open.size}
                  onClick={() => {
                    const selected = visible.find(item => item.span_id === active);
                    if (selected?.parent_span_id) select(selected.parent_span_id);
                    setOpen(new Set());
                  }}
                >
                  折叠全部
                </Button>
              </div>
              <div className="trace-row trace-axis">
                <span>事件</span>
                <div>
                  {(trace.range_ms ? [0, 0.25, 0.5, 0.75, 1] : [0]).map(tick => (
                    <span key={tick} style={{ left: `${tick * 100}%` }}>
                      {duration(Math.round(trace.range_ms * tick))}
                    </span>
                  ))}
                </div>
                <span>耗时</span>
                <span>结果</span>
              </div>
              {visible.map(item => {
                const offset = item.start_offset_ms;
                const left = offset === null ? undefined : (offset / range) * 100;
                const width = ((item.duration_ms ?? 0) / range) * 100;
                const isLoop = item.kind === "loop";
                const rowMarks = marksBySpan.get(item.span_id) ?? [];
                return (
                  <button
                    type="button"
                    key={item.span_id}
                    className="trace-row trace-event"
                    data-kind={item.kind}
                    data-status={item.status}
                    aria-current={active === item.span_id}
                    aria-expanded={isLoop ? open.has(item.span_id) : undefined}
                    onClick={() => toggle(item)}
                  >
                    <span
                      className={
                        item.parent_span_id && loops.has(item.parent_span_id)
                          ? "trace-child"
                          : ""
                      }
                    >
                      <small>
                        {isLoop ? (open.has(item.span_id) ? "▾" : "▸") : kinds[item.kind]}
                      </small>
                      <b>{item.name}</b>
                      {isLoop && <small>{item.tool_calls ?? 0} 次调用</small>}
                      {rowMarks.map(mark => (
                        <i
                          key={`${mark.span_id}-${mark.check_id ?? mark.verdict}`}
                          className="trace-check-mark"
                          title={
                            mark.check_id
                              ? `${mark.check_id} · ${mark.verdict}`
                              : mark.verdict
                          }
                          style={{ background: markColor[mark.verdict] }}
                        />
                      ))}
                    </span>
                    <span
                      className="trace-track"
                      aria-label={
                        offset === null ? "时间未记录" : `距轮次开始 ${duration(offset)}`
                      }
                    >
                      {left !== undefined && (
                        <i
                          style={{ left: `${left}%`, width: `${width}%` }}
                          data-mark={
                            item.kind === "gate"
                              ? "gate"
                              : item.point || !width
                                ? "point"
                                : "span"
                          }
                        />
                      )}
                    </span>
                    <span>{duration(item.duration_ms)}</span>
                    <span>{statusLabel(item.status)}</span>
                  </button>
                );
              })}
              {missing.map(row => (
                <button
                  type="button"
                  key={row.id}
                  className="trace-row trace-event"
                  data-kind="missing"
                  data-status="missing"
                  aria-current={active === row.id}
                  onClick={() => select(row.id)}
                >
                  <span>
                    <small>预期</small>
                    <b>{row.name}</b>
                  </span>
                  <span className="trace-track" aria-label="未出现">
                    <i data-mark="missing" />
                  </span>
                  <span>—</span>
                  <span>未出现</span>
                </button>
              ))}
            </>
          )}
        </>
      )}
      {detail}
    </section>
  );
}
