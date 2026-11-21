import { useState } from "react";
import { TraceTimelineView } from "../components/trace/TraceTimelineView";
import { failedStatuses } from "../components/trace/labels";
import type { TraceEvent, TurnTrace } from "../components/trace/types";
import { type Rule, type RunDetail, type TraceArchive, runUrl, url, useResource } from "./api";
import { Data, displayDate, Notice } from "./ui";

export const evidenceLabels: Record<string, string> = {
  author_message: "作者原话", workspace_before: "运行前文件快照", workspace_after: "运行后文件快照",
  modules_after: "模块库与作品绑定", structure_text: "实际保存的 STRUCTURE.md", prose_text: "实际保存的正文",
  assistant_reply: "Agent 当轮公开回复", execution_error: "执行错误", preflight: "预检记录",
};

export function versionLabel(version: string) {
  return version === "legacy-original" ? "历史原始版" : version === "legacy-current" ? "历史核对版" : version;
}

function property(value: unknown, key: string): unknown {
  return value != null && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, key) ? (value as Rule)[key] : undefined;
}

export function evidenceValue(data: Rule, ref: string) {
  const exact = property(data, ref);
  if (exact !== undefined) return exact;
  const slash = ref.indexOf("/");
  if (slash < 0) return undefined;
  return property(property(data, ref.slice(0, slash)), ref.slice(slash + 1));
}

export function Evidence({ detail, version, change, reference, unbound }: {
  detail: RunDetail; version: string; change: (version: string) => void; reference: string; unbound: boolean;
}) {
  const { evidence_versions, available_evidence } = detail;
  const needsChoice = unbound && !version;
  const response = useResource<Rule>(!needsChoice && (evidence_versions.length || available_evidence.length)
    ? url(runUrl(detail.run.run_id, "/evidence"), { version }) : null);
  return <div className="wb-stack">
    {evidence_versions.length > 0 && <label>证据版本<select value={version} onChange={e => change(e.target.value)}>
      <option value="">{unbound ? "请选择证据版本" : "当前证据"}</option>{evidence_versions.map(v => <option key={v} value={v}>{versionLabel(v)}</option>)}
    </select></label>}
    <p className="muted">公开回复不等于落盘证明。未采集与已记录为空分开显示。</p>
    {unbound && <p className="muted">服务未提供所选历史报告绑定的证据版本，不能把手动选择的版本视为原判定依据。</p>}
    <Notice {...response} />
    {reference && !needsChoice && <div className="wb-target"><strong>引用 · {reference}</strong><Data value={response.data ? evidenceValue(response.data, reference) : undefined} /></div>}
    {!needsChoice && !response.loading && !response.error && (response.data ?
      [...new Set([...Object.keys(evidenceLabels), ...available_evidence, ...Object.keys(response.data)])].map(key =>
        <details key={key}>
          <summary>{evidenceLabels[key] ?? key} {property(response.data, key) == null && <small>· 未记录</small>}</summary>
          <Data value={property(response.data, key)} />
        </details>) : <p className="muted">本轮没有可读取的归档证据。</p>)}
  </div>;
}

export type TraceTarget = { id: string; write: boolean; revision: number };

function isIssue(event: TraceEvent) {
  return failedStatuses.has(event.status) || Boolean(event.error_code);
}

export function TracePanel({ detail, lane, changeLane, target, jump }: {
  detail: RunDetail; lane: "flow" | "agent"; changeLane: (lane: "flow" | "agent") => void;
  target: TraceTarget; jump: (key: string) => void;
}) {
  const [filter, setFilter] = useState({ value: false, revision: target.revision });
  const onlyIssues = filter.revision === target.revision && filter.value;
  const response = useResource<TraceArchive>(lane === "agent" ? runUrl(detail.run.run_id, "/trace") : null, {
    refreshKey: detail.run.execution_status,
  });
  const [expanded, setExpanded] = useState<boolean>();
  const records = detail.milestones.filter(node => !onlyIssues || /warn|error|fail|block/i.test(node.type));
  const events = response.data?.events ?? [];
  const candidate = target.write ? events.find(event => event.kind === "write" || (event.kind === "tool_call" && /write|save|写入|保存/i.test(event.name)))
    : events.find(event => event.span_id === target.id || event.start_event_id === target.id || event.finish_event_id === target.id);
  return <div className="wb-stack">
    <div className="wb-actions">
      <button aria-pressed={lane === "flow"} onClick={() => changeLane("flow")}>评测流程</button>
      <button aria-pressed={lane === "agent"} onClick={() => changeLane("agent")}>Agent 执行</button>
      <label><input type="checkbox" checked={onlyIssues} onChange={e => setFilter({ value: e.target.checked, revision: target.revision })} />仅看问题</label>
    </div>
    {lane === "flow" ? <>
      {records.length > 0 && <button type="button" className="wb-fit" onClick={() => setExpanded(value => !value)}>{expanded ? "收起全部" : "展开全部"}</button>}
      {records.length ? <ol className="wb-milestones" key={String(expanded)}>{records.map((node, index) => <li key={`${index}:${node.type}`}>
        <details open={expanded}><summary>{node.summary}</summary>
          <small>{node.type} · {displayDate(node.at)}</small>
          {node.trace_refs?.map(id => <button key={id} onClick={() => jump(id)}>定位 Trace · {id}</button>)}
        </details>
      </li>)}</ol> : <p className="muted">{onlyIssues ? "没有已记录的评测问题。" : "未记录评测流程事件。"}</p>}
    </> : <>
      <Notice {...response} />
      {response.error && <button className="wb-fit" onClick={response.refresh}>重试读取 Trace</button>}
      {response.data && (events.length ? <>
        {(target.id || target.write) && !candidate && <p className="muted">归档 Trace 中找不到所引用的事件。</p>}
        <AgentLane key={target.revision} archive={response.data} detail={detail} onlyIssues={onlyIssues} initial={candidate} />
      </> : <p className="muted">此运行未归档 Agent Trace。</p>)}
    </>}
  </div>;
}

function AgentLane({ archive, detail, onlyIssues, initial }: { archive: TraceArchive; detail: RunDetail; onlyIssues: boolean; initial?: TraceEvent }) {
  const [span, setSpan] = useState(initial?.span_id ?? "");
  const visible = onlyIssues ? archive.events.filter(isIssue) : archive.events;
  const selected = visible.find(event => event.span_id === span);
  const parentLoop = archive.events.find(event => event.span_id === initial?.parent_span_id && event.kind === "loop");
  const trace: TurnTrace = {
    events: visible,
    range_ms: Math.max(0, ...archive.events.map(event => (event.start_offset_ms ?? 0) + (event.duration_ms ?? 0))),
    turn: {
      turn_id: detail.run.turn_id ?? "", seq: 0, text: "归档 Agent 执行", status: detail.run.execution_status,
      duration_ms: null, started_at: detail.run.started_at ?? "", finished_at: null, trace_available: true,
    },
    cursor: "", usage: { input: null, output: null, partial: false },
  };
  if (!visible.length) return <p className="muted">没有已记录的 Agent 执行问题。</p>;
  const records = selected ? [...new Set([selected.start_event_id, selected.finish_event_id])]
    .flatMap(id => id && Object.prototype.hasOwnProperty.call(archive.details, id) ? [archive.details[id]] : []) : [];
  return <TraceTimelineView trace={trace} activeSpanId={selected?.span_id ?? ""} onSelect={setSpan}
    initiallyOpenLoop={parentLoop?.span_id} detail={selected && <section className="wb-target" aria-label="选中事件详情">
      <h3>{selected.name} · 事件详情</h3>{selected.summary && <p>{selected.summary}</p>}
      <p className="muted">{selected.span_id}{selected.error_code ? ` · ${selected.error_code}` : ""}</p>
      {selected.retry_of_span_id && <button onClick={() => setSpan(selected.retry_of_span_id!)}>定位重试来源</button>}
      <Data value={records.length ? records : undefined} />
    </section>} />;
}
