import { useCallback, useEffect, useMemo, useState } from "react";

import {
  activityError, activityStream, traceTurns, turnTrace,
  type TraceEvent, type TraceTurn, type TurnTrace,
} from "../../api/activity";
import { Button } from "../ui/Button";
import {
  TraceDetailView,
  TraceTimelineView,
  productTraceLoader,
} from "../trace";
import { duration, localTime } from "../trace/labels";

interface Scope {
  workspace: string;
  conversation: string;
}

const watching = (trace: TurnTrace) =>
  ["running", "paused", "needs_input"].includes(trace.turn.status);

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
    {selected && <LiveTraceTimeline key={selected} workspace={workspace} conversation={conversation}
      turnId={selected} updateTurn={updateTurn} />}
  </>;
}

function LiveTraceTimeline({ workspace, conversation, turnId, updateTurn }: Scope & {
  turnId: string;
  updateTurn: (turn: TraceTurn) => void;
}) {
  const [trace, setTrace] = useState<TurnTrace>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [active, setActive] = useState("");
  const loader = useMemo(() => productTraceLoader(workspace), [workspace]);

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

  const detailItem = trace?.events.find((item: TraceEvent) => item.span_id === active);

  return <TraceTimelineView
    trace={trace}
    error={error || undefined}
    loading={!trace && !error}
    activeSpanId={active}
    onSelect={setActive}
    onRetry={() => setReload(value => value + 1)}
    detail={detailItem ? <TraceDetailView key={detailItem.span_id} item={detailItem} loader={loader} /> : null}
  />;
}
