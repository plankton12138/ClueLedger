import { useCallback, useEffect, useState } from "react";

import {
  activityError, activityRequest, activityStream, traceTurns, turnTrace,
  type TraceEvent, type TraceTurn, type TurnTrace,
} from "../../api/activity";
import { Button } from "../ui/Button";

const kinds: Record<TraceEvent["kind"], string> = {
  loop: "Loop", model: "Model", tool_call: "Tool call", gate: "Gate",
  retrieval: "Retrieval", write: "Write", reply: "Reply",
};
const statuses: Record<string, string> = {
  completed: "已完成", recovered: "已恢复", completed_with_errors: "含失败",
  failed: "失败", passed: "通过", blocked: "拦截", skipped: "跳过",
  running: "进行中", stopped: "已停止", interrupted: "已中断",
  paused: "已暂停", needs_input: "等待作者", truncated: "已截断", partial: "部分完成",
};
const duration = (ms: number | null) =>
  ms === null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const localTime = (at: string | null) => at ? new Date(at).toLocaleString("zh-CN", { hour12: false }) : "—";
const statusLabel = (status: string) => statuses[status] ?? status;
const watching = (trace: TurnTrace) => ["running", "paused", "needs_input"].includes(trace.turn.status);

interface Scope {
  workspace: string;
  conversation: string;
}

export default function RuntimeTrace({ workspace, currentConversation, conversations }: {
  workspace: string;
  currentConversation: string | null;
  conversations: { id: string; title: string }[];
}) {
  const [picked, setPicked] = useState(currentConversation ?? "");
  const conversation = conversations.find(item => item.id === picked) ?? conversations[0];
  return <div className="trace-workbench">
    <label className="trace-session">
      <span>会话</span>
      <select value={conversation?.id ?? ""} onChange={event => setPicked(event.target.value)}>
        {!conversation && <option value="">暂无会话</option>}
        {conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
    </label>
    {conversation && <ConversationTrace key={conversation.id} workspace={workspace} conversation={conversation.id} />}
  </div>;
}

function ConversationTrace({ workspace, conversation }: Scope) {
  const [turns, setTurns] = useState<TraceTurn[] | null>(null);
  const [selected, setSelected] = useState("");
  const [before, setBefore] = useState<number | null>(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const request = new AbortController();
    setLoading(true);
    setError("");
    traceTurns(workspace, conversation, page, request.signal).then(result => {
      if (request.signal.aborted) return;
      setTurns(current => page ? [...(current ?? []), ...result.items] : result.items);
      setSelected(current => current || result.items[0]?.turn_id || "");
      setBefore(result.next_before);
    }).catch(reason => {
      if (!request.signal.aborted) setError(activityError(reason));
    }).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [workspace, conversation, page]);
  const updateTurn = useCallback((turn: TraceTurn) => {
    setTurns(current => current?.map(item => item.turn_id === turn.turn_id ? turn : item) ?? null);
  }, []);

  return <>
    <aside className="trace-turns" aria-label="会话轮次">
      <h3>Turns</h3>
      {turns?.map(item => <button type="button" key={item.turn_id}
        aria-pressed={selected === item.turn_id} onClick={() => setSelected(item.turn_id)}>
        <span>{item.text || "无请求文本"}</span>
        <small>{localTime(item.started_at)} · {duration(item.duration_ms)}</small>
      </button>)}
      {loading && <p role="status">正在读取轮次…</p>}
      {error && <p role="alert">{error}</p>}
      {turns?.length === 0 && <p>该会话暂无 Turn，可选择其他会话。</p>}
      {before !== null && before !== 0 && <Button disabled={loading} onClick={() => setPage(before)}>更早的轮次</Button>}
    </aside>
    {selected && <TraceTimeline key={selected} workspace={workspace} conversation={conversation}
      turnId={selected} updateTurn={updateTurn} />}
  </>;
}

function TraceTimeline({ workspace, conversation, turnId, updateTurn }: Scope & {
  turnId: string;
  updateTurn: (turn: TraceTurn) => void;
}) {
  const [trace, setTrace] = useState<TurnTrace>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [active, setActive] = useState("");

  useEffect(() => {
    const request = new AbortController();
    async function refresh() {
      const snapshot = await turnTrace(workspace, conversation, turnId, request.signal);
      if (!request.signal.aborted) {
        setTrace(snapshot);
        updateTurn(snapshot.turn);
        setError("");
      }
      return snapshot;
    }
    void (async () => {
      for (let attempt = 0; attempt < 3 && !request.signal.aborted; attempt += 1) {
        try {
          let snapshot = await refresh();
          if (!watching(snapshot) || request.signal.aborted) return;
          for await (const page of activityStream(workspace, { conversation_id: conversation, turn_id: turnId },
            snapshot.cursor, request.signal)) {
            if (!page.reset && !page.items.length) continue;
            snapshot = await refresh();
            if (!watching(snapshot)) return;
          }
        } catch (reason) {
          if (request.signal.aborted) return;
          if (attempt === 2) setError(activityError(reason));
          else await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
        }
      }
    })();
    return () => request.abort();
  }, [workspace, conversation, turnId, reload, updateTurn]);

  const events = trace?.events ?? [];
  const loops = new Set(events.filter(item => item.kind === "loop").map(item => item.span_id));
  const children = new Map<string, TraceEvent[]>();
  for (const item of events) {
    if (!item.parent_span_id || !loops.has(item.parent_span_id)) continue;
    const group = children.get(item.parent_span_id) ?? [];
    group.push(item);
    children.set(item.parent_span_id, group);
  }
  const roots = events.filter(item => !item.parent_span_id || !loops.has(item.parent_span_id));
  const visible = roots.flatMap(item => open.has(item.span_id) ? [item, ...(children.get(item.span_id) ?? [])] : [item]);
  const detail = visible.find(item => item.span_id === active);
  const range = trace?.range_ms || 1;

  function toggle(item: TraceEvent) {
    setActive(item.span_id);
    if (item.kind !== "loop") return;
    setOpen(current => {
      const next = new Set(current);
      if (next.has(item.span_id)) next.delete(item.span_id);
      else next.add(item.span_id);
      return next;
    });
  }

  return <section className="trace-timeline" aria-label="执行时间线">
    {error && <div role="alert">{error} <Button onClick={() => setReload(value => value + 1)}>重新连接</Button></div>}
    {!trace && !error && <p role="status">正在读取运行追踪…</p>}
    {trace && <>
      <h3>{trace.turn.text || "无请求文本"}</h3>
      <div className="trace-stats">
        <span>{statusLabel(trace.turn.status)}</span>
        <span>总耗时 <strong>{duration(trace.turn.duration_ms)}</strong></span>
        <span>{loops.size} Loops</span>
        <span>{events.filter(item => item.kind === "tool_call").length} 次工具调用</span>
        <span>Tokens {trace.usage.input ?? "—"} 入 / {trace.usage.output ?? "—"} 出
          {trace.usage.partial ? "（部分记录）" : trace.usage.input === null && trace.usage.output === null ? "（未记录）" : ""}</span>
      </div>
      {!trace.turn.trace_available ? <p>该轮未记录运行追踪，可在「事件流」查看已有记录。</p>
        : events.length === 0 ? <p>该轮暂无执行事件。</p>
        : <>
          <div className="trace-toolbar">
            <strong>执行时间线</strong>
            <Button disabled={!loops.size || open.size === loops.size} onClick={() => setOpen(loops)}>展开全部</Button>
            <Button disabled={!open.size} onClick={() => {
              if (detail?.parent_span_id) setActive(detail.parent_span_id);
              setOpen(new Set());
            }}>折叠全部</Button>
          </div>
          <div className="trace-row trace-axis">
            <span>事件</span>
            <div>{(trace.range_ms ? [0, .25, .5, .75, 1] : [0]).map(tick =>
              <span key={tick} style={{ left: `${tick * 100}%` }}>{duration(Math.round(trace.range_ms * tick))}</span>)}</div>
            <span>耗时</span><span>结果</span>
          </div>
          {visible.map(item => {
            const offset = item.start_offset_ms;
            const left = offset === null ? undefined : offset / range * 100;
            const width = (item.duration_ms ?? 0) / range * 100;
            const isLoop = item.kind === "loop";
            return <button type="button" key={item.span_id} className="trace-row trace-event"
              data-kind={item.kind} data-status={item.status} aria-current={active === item.span_id}
              aria-expanded={isLoop ? open.has(item.span_id) : undefined}
              onClick={() => toggle(item)}>
              <span className={item.parent_span_id && loops.has(item.parent_span_id) ? "trace-child" : ""}>
                <small>{isLoop ? (open.has(item.span_id) ? "▾" : "▸") : kinds[item.kind]}</small>
                <b>{item.name}</b>{isLoop && <small>{item.tool_calls ?? 0} 次调用</small>}
              </span>
              <span className="trace-track" aria-label={offset === null ? "时间未记录" : `距轮次开始 ${duration(offset)}`}>
                {left !== undefined && <i style={{ left: `${left}%`, width: `${width}%` }}
                  data-mark={item.kind === "gate" ? "gate" : item.point || !width ? "point" : "span"} />}
              </span>
              <span>{duration(item.duration_ms)}</span><span>{statusLabel(item.status)}</span>
            </button>;
          })}
        </>}
    </>}
    {detail && <TraceDetail key={detail.span_id} workspace={workspace} item={detail} />}
  </section>;
}

function TraceDetail({ workspace, item }: { workspace: string; item: TraceEvent }) {
  const [source, setSource] = useState<Record<string, unknown>[]>([]);
  const [attachment, setAttachment] = useState<{ label: string; value: unknown }>();
  const [attachmentLoading, setAttachmentLoading] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { start_event_id: start, finish_event_id: finish } = item;
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    Promise.all([...new Set([start, finish].filter(Boolean))].map(id =>
      activityRequest<Record<string, unknown>>(workspace, `/${encodeURIComponent(id!)}`)))
      .then(value => { if (current) setSource(value); })
      .catch(reason => { if (current) setError(activityError(reason)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [workspace, start, finish]);
  const first = source[0];
  const last = source.at(-1);
  return <div className="trace-inspector">
    <h4>{kinds[item.kind]} · {item.name} · {statusLabel(item.status)}</h4>
    <p>{item.point ? `发生于 ${localTime(item.started_at)}`
      : `开始 ${localTime(item.started_at)} · 结束 ${localTime(item.finished_at)}`}</p>
    {item.retry_of_span_id && <p>重试来源 {item.retry_of_span_id}</p>}
    {item.summary && <p>{item.error_code ? `[${item.error_code}] ${String(first?.where ?? item.name)}：` : ""}
      {item.summary}{first?.fix ? `。建议：${String(first.fix)}` : ""}</p>}
    {loading && <p role="status">正在读取事件详情…</p>}
    {error && <p role="alert">{error}</p>}
    {first?.args != null && <pre>{JSON.stringify(first.args, null, 2)}</pre>}
    {item.kind === "model" && <p>用途 {String(first?.stage ?? "—")} · 结束原因 {String(last?.stop_reason ?? "—")}
      {item.usage && ` · Tokens ${item.usage.input ?? "—"} 入 / ${item.usage.output ?? "—"} 出`}</p>}
    {item.kind === "model" && typeof first?.tokens_before === "number" && typeof first.tokens_after === "number" &&
      <p>重试前后估算输入 {first.tokens_before} → {first.tokens_after} Tokens</p>}
    {item.kind === "gate" && first?.where != null && !item.error_code && <p>{String(first.where)}</p>}
    {item.kind === "gate" && typeof first?.input_tokens === "number" && typeof first.limit_tokens === "number" &&
      <p>估算输入 {first.input_tokens} / 上限 {first.limit_tokens} Tokens</p>}
    {item.kind === "retrieval" && Array.isArray(first?.provided) && <pre>{JSON.stringify(first.provided, null, 2)}</pre>}
    <div className="trace-toolbar">
      {([[first?.input_detail, "输入详情"], [last?.result_detail, "结果详情"]] as const)
        .map(([key, label]) => typeof key === "string" && <Button key={label} disabled={attachmentLoading} onClick={() => {
          setError("");
          setAttachment(undefined);
          setAttachmentLoading(true);
          void activityRequest(workspace, `/attachments/${encodeURIComponent(key)}`)
            .then(value => setAttachment({ label, value })).catch(reason => setError(activityError(reason)))
            .finally(() => setAttachmentLoading(false));
        }}>{label}</Button>)}
    </div>
    {attachment && <pre>{attachment.label}{"\n"}{typeof attachment.value === "string"
      ? attachment.value : JSON.stringify(attachment.value, null, 2)}</pre>}
  </div>;
}
