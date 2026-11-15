import { useCallback, useEffect, useMemo, useState } from "react";

import { TraceDetailView, TraceTimelineView } from "../components/trace";
import type { TraceEvent, TurnTrace } from "../components/trace";

import {
  type AttemptPayload,
  type Attribution,
  type DiffView,
  type Report,
  type Review,
  type RunSummary,
  fetchAttempt,
  fetchDiff,
  fetchReport,
  fetchReviews,
  fetchRuns,
  fetchTrace,
  mergeScore,
  saveReviews,
} from "./api";
import { evidenceTraceLoader } from "./evidenceLoader";
import { checkLens, firstSpanRef, hasFileRef, marksFromChecks, missingFromChecks } from "./marks";

function readHashRun(): string | null {
  const match = location.hash.match(/^#\/?runs\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function writeHashRun(runId: string | null) {
  const next = runId ? `#/runs/${encodeURIComponent(runId)}` : "";
  const current = location.hash.replace(/^#/, "") ? location.hash : "";
  if (current === next) return;
  if (next) location.hash = next;
  else history.pushState(null, "", `${location.pathname}${location.search}`);
}

const VERDICT: Record<string, string> = {
  pass: "通过",
  fail: "失败",
  skipped: "跳过",
  pending_review: "待复核",
  not_reached: "未到达",
};

const COLOR: Record<string, string> = {
  pass: "var(--wb-ok)",
  fail: "var(--wb-bad)",
  skipped: "var(--wb-skip)",
  pending_review: "var(--wb-warn)",
  not_reached: "var(--wb-skip)",
  infrastructure_error: "var(--wb-bad)",
  not_run: "var(--wb-skip)",
};

type Filter = "all" | "fail" | "pending_review";
type Tab = "checks" | "diff" | "review" | "attr";
type Selection = { caseId: string; attempt: number } | null;

function outcomeColor(status: string, outcome?: string | null) {
  if (status === "infrastructure_error" || status === "not_run") return COLOR[status];
  return COLOR[outcome || "skipped"] || COLOR.skipped;
}

function highlightRegion(text: string, from?: string, to?: string, preserve: string[] = []) {
  if (!from || !text.includes(from)) return text;
  const start = text.indexOf(from);
  const end = to && to !== "END" && text.includes(to, start)
    ? text.indexOf(to, start)
    : text.length;
  const head = text.slice(0, start);
  let mid = text.slice(start, end);
  for (const frag of preserve) {
    if (!frag || !mid.includes(frag)) continue;
    mid = mid.split(frag).join(`<span class="wb-keep">${frag}</span>`);
  }
  const tail = text.slice(end);
  return `${head}<span class="wb-region">${mid}</span>${tail}`;
}

export function WorkbenchApp() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsRoot, setRunsRoot] = useState("");
  const [runId, setRunId] = useState<string | null>(() => readHashRun());
  const [report, setReport] = useState<Report | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [attributions, setAttributions] = useState<Attribution[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selection, setSelection] = useState<Selection>(null);
  const [view, setView] = useState<"overview" | "attempt">("overview");

  const loadRuns = useCallback(async () => {
    setError("");
    try {
      const payload = await fetchRuns();
      setRuns(payload.runs || []);
      setRunsRoot(payload.runs_root || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  const openRun = useCallback((id: string | null) => {
    setError("");
    setReport(null);
    setReviews([]);
    setAttributions([]);
    setSelection(null);
    setView("overview");
    setStatus("");
    setRunId(id);
    writeHashRun(id);
    if (!id) void loadRuns();
  }, [loadRuns]);

  const reload = useCallback(async () => {
    if (!runId) return;
    setError("");
    try {
      const nextReport = await fetchReport(runId);
      const payload = await fetchReviews(runId);
      setReport(nextReport);
      setReviews(payload.reviews || []);
      setAttributions(payload.attributions || []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [runId]);

  useEffect(() => {
    void loadRuns();
    const syncHash = () => {
      const next = readHashRun();
      setRunId(current => (current === next ? current : next));
    };
    window.addEventListener("hashchange", syncHash);
    window.addEventListener("popstate", syncHash);
    return () => {
      window.removeEventListener("hashchange", syncHash);
      window.removeEventListener("popstate", syncHash);
    };
  }, [loadRuns]);

  useEffect(() => {
    if (!runId) {
      setReport(null);
      setSelection(null);
      setView("overview");
      return;
    }
    let cancelled = false;
    setError("");
    void (async () => {
      try {
        const nextReport = await fetchReport(runId);
        const payload = await fetchReviews(runId);
        if (cancelled) return;
        setReport(nextReport);
        setReviews(payload.reviews || []);
        setAttributions(payload.attributions || []);
        // 仅首次进入该运行时，单案例直接进试验；之后「总览 / 返回」不再被拽回去
        if ((nextReport.cases || []).length === 1) {
          const only = nextReport.cases![0];
          const first = only.attempts?.[0];
          if (first) {
            setSelection({ caseId: only.case_id, attempt: first.attempt });
            setView("attempt");
          }
        } else {
          setSelection(null);
          setView("overview");
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  async function persistAndScore(nextReviews: Review[], nextAttrs: Attribution[]) {
    if (!runId) return;
    setStatus("");
    try {
      const saved = await saveReviews(runId, { reviews: nextReviews, attributions: nextAttrs });
      setReviews(saved.reviews);
      setAttributions(saved.attributions);
      const scored = await mergeScore(runId);
      setReport(scored);
      setStatus(`已合并；门禁 ${scored.gate?.passed ? "通过" : "不通过"}`);
      void loadRuns();
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason));
    }
  }

  if (error && !runId) {
    return <div className="wb-root"><p className="wb-error" role="alert">{error}</p></div>;
  }

  if (!runId) {
    return (
      <div className="wb-root">
        <header className="wb-header">
          <h1>评测工作台</h1>
          <span className="wb-chip">本机已落盘运行</span>
          <span className="wb-chip btn" onClick={() => void loadRuns()} role="button">刷新列表</span>
        </header>
        <RunPicker runs={runs} runsRoot={runsRoot} onOpen={openRun} />
      </div>
    );
  }

  if (error) return <div className="wb-root"><p className="wb-error" role="alert">{error}</p></div>;
  if (!report) return <div className="wb-root"><p className="wb-muted" style={{ padding: "1rem" }}>正在读取报告…</p></div>;

  const identity = report.identity || {};
  const gate = report.gate || {};

  return (
    <div className="wb-root">
      <header className="wb-header">
        <h1>评测工作台</h1>
        <span className="wb-chip mono">{runId}</span>
        <span className="wb-chip">{report.mode === "gate" ? "门禁 · 每案 3 次" : "冒烟 · 每案 1 次"}</span>
        {identity.git_commit != null && <span className="wb-chip mono">{String(identity.git_commit).slice(0, 8)}</span>}
        {identity.model != null && <span className="wb-chip">{String(identity.model)}</span>}
        {identity.budget_used != null && (
          <span className="wb-chip">预算 {String(identity.budget_used)}{identity.budget_max != null ? ` / ${String(identity.budget_max)}` : ""}</span>
        )}
        <span className={`wb-chip ${gate.passed ? "ok" : "bad"}`}>{gate.passed ? "门禁通过" : "门禁不通过"}</span>
        {report.baseline_compare && (
          <span className="wb-chip">对比基线 {String((report.baseline_compare.baseline_identity || {}).git_commit || "").slice(0, 8) || "已绑定"}</span>
        )}
        <button type="button" className="wb-chip btn" onClick={() => openRun(null)}>← 运行列表</button>
        {view === "attempt" && (
          <button
            type="button"
            className="wb-chip ghost"
            onClick={() => {
              setSelection(null);
              setView("overview");
            }}
          >
            本案总览
          </button>
        )}
        <button type="button" className="wb-chip ghost" onClick={() => void reload()}>刷新</button>
      </header>

      {view === "overview" || !selection ? (
        <Overview
          report={report}
          filter={filter}
          onFilter={setFilter}
          onOpen={(caseId, attempt) => {
            setSelection({ caseId, attempt });
            setView("attempt");
          }}
        />
      ) : (
        <AttemptView
          runId={runId}
          report={report}
          selection={selection}
          filter={filter}
          onFilter={setFilter}
          onSelect={setSelection}
          reviews={reviews}
          attributions={attributions}
          status={status}
          onSaveReviews={(next, attrs) => void persistAndScore(next, attrs)}
          onReviewsChange={setReviews}
          onAttributionsChange={setAttributions}
        />
      )}
    </div>
  );
}

function RunPicker({
  runs,
  runsRoot,
  onOpen,
}: {
  runs: RunSummary[];
  runsRoot: string;
  onOpen: (id: string) => void;
}) {
  if (!runs.length) {
    return (
      <div className="wb-overview">
        <p className="wb-muted">还没有落盘运行。</p>
        <p className="wb-muted">目录：{runsRoot || "data/evaluation/runs"}</p>
        <p className="wb-muted">先执行 evaluation run 写出报告后，再回到这里选择。</p>
      </div>
    );
  }
  return (
    <div className="wb-overview">
      <p className="wb-muted" style={{ marginTop: 0 }}>选择一次运行查看记录 · {runsRoot}</p>
      <table className="wb-matrix">
        <thead>
          <tr>
            <th>运行</th>
            <th>模式</th>
            <th>门禁</th>
            <th>摘要</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(item => {
            const summary = item.summary || {};
            const when = item.mtime ? new Date(item.mtime * 1000).toLocaleString() : "—";
            return (
              <tr key={item.id} className="click" onClick={() => onOpen(item.id)} style={{ cursor: "pointer" }}>
                <td><code>{item.id}</code></td>
                <td>{item.mode || "—"}</td>
                <td className={item.gate_passed ? "wb-delta better" : "wb-delta worse"}>
                  {item.gate_passed == null ? "—" : item.gate_passed ? "通过" : "不通过"}
                </td>
                <td className="wb-muted">
                  通过 {summary.pass || 0} · 失败 {summary.fail || 0} · 待复核 {summary.pending_review || 0}
                </td>
                <td className="wb-muted">{when}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Overview({
  report,
  filter,
  onFilter,
  onOpen,
}: {
  report: Report;
  filter: Filter;
  onFilter: (value: Filter) => void;
  onOpen: (caseId: string, attempt: number) => void;
}) {
  const baseline = Object.fromEntries(
    (report.baseline_compare?.cases || []).map(item => [item.case_id, item]),
  );
  const maxAttempts = Math.max(1, ...((report.cases || []).map(item => item.attempts?.length || 0)));

  return (
    <div className="wb-overview">
      <div className="wb-filters" style={{ marginBottom: 12, maxWidth: 360 }}>
        {(["all", "fail", "pending_review"] as Filter[]).map(item => (
          <button key={item} type="button" className={filter === item ? "on" : ""} onClick={() => onFilter(item)}>
            {item === "all" ? "全部" : item === "fail" ? "失败" : "待复核"}
          </button>
        ))}
      </div>
      <table className="wb-matrix">
        <thead>
          <tr>
            <th>案例</th>
            {Array.from({ length: maxAttempts }, (_, index) => <th key={index}>#{index + 1}</th>)}
            <th>基线</th>
          </tr>
        </thead>
        <tbody>
          {(report.cases || [])
            .filter(caseItem => {
              if (filter === "all") return true;
              return (caseItem.attempts || []).some(row =>
                filter === "fail"
                  ? row.outcome === "fail" || caseItem.must_failed
                  : row.outcome === "pending_review",
              );
            })
            .map(caseItem => {
              const cmp = baseline[caseItem.case_id];
              return (
                <tr key={caseItem.case_id}>
                  <td><code>{caseItem.case_id}</code></td>
                  {Array.from({ length: maxAttempts }, (_, index) => {
                    const row = caseItem.attempts?.[index];
                    if (!row) return <td key={index} className="wb-muted">—</td>;
                    return (
                      <td key={index}>
                        <button
                          type="button"
                          title={`${row.status} · ${row.outcome || "-"}`}
                          style={{ background: outcomeColor(row.status, row.outcome) }}
                          onClick={() => onOpen(caseItem.case_id, row.attempt)}
                        />
                      </td>
                    );
                  })}
                  <td className={`wb-delta ${cmp?.delta || ""}`}>
                    {cmp ? `${cmp.delta}（${cmp.quality_pass_count}/${cmp.baseline_quality_pass_count}）` : "—"}
                  </td>
                </tr>
              );
            })}
        </tbody>
      </table>
      {(report.gate?.blockers || []).length > 0 && (
        <ul className="wb-muted">
          {report.gate!.blockers!.map((item, index) => (
            <li key={index}>[{item.kind}] {item.case_id}: {item.detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AttemptView({
  runId,
  report,
  selection,
  filter,
  onFilter,
  onSelect,
  reviews,
  attributions,
  status,
  onSaveReviews,
  onReviewsChange,
  onAttributionsChange,
}: {
  runId: string;
  report: Report;
  selection: { caseId: string; attempt: number };
  filter: Filter;
  onFilter: (value: Filter) => void;
  onSelect: (value: Selection) => void;
  reviews: Review[];
  attributions: Attribution[];
  status: string;
  onSaveReviews: (reviews: Review[], attributions: Attribution[]) => void;
  onReviewsChange: (reviews: Review[]) => void;
  onAttributionsChange: (attributions: Attribution[]) => void;
}) {
  const [payload, setPayload] = useState<AttemptPayload | null>(null);
  const [trace, setTrace] = useState<TurnTrace>();
  const [diff, setDiff] = useState<DiffView | null>(null);
  const [active, setActive] = useState("");
  const [activeCheck, setActiveCheck] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("checks");
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoadError("");
    setTrace(undefined);
    setDiff(null);
    setActive("");
    setActiveCheck(null);
    void (async () => {
      try {
        const next = await fetchAttempt(runId, selection.caseId, selection.attempt);
        if (!alive) return;
        setPayload(next);
        const turnId = next.turns[0]?.turn_id || next.meta.turns?.[0]?.turn_id;
        if (turnId) {
          const nextTrace = await fetchTrace(runId, selection.caseId, selection.attempt, turnId);
          if (alive) setTrace(nextTrace);
        }
        if (next.has_evidence) {
          const nextDiff = await fetchDiff(runId, selection.caseId, selection.attempt);
          if (alive) setDiff(nextDiff);
        }
      } catch (reason) {
        if (alive) setLoadError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => { alive = false; };
  }, [runId, selection.caseId, selection.attempt]);

  const loader = useMemo(
    () => evidenceTraceLoader(runId, selection.caseId, selection.attempt),
    [runId, selection.caseId, selection.attempt],
  );
  const checks = payload?.row.checks || [];
  const marks = useMemo(() => marksFromChecks(checks), [checks]);
  const missing = useMemo(() => missingFromChecks(checks), [checks]);
  const detailItem = trace?.events.find((item: TraceEvent) => item.span_id === active);
  const stages = payload?.row.stages || [];
  const firstFailed = payload?.row.first_failed_stage;
  const ask = payload?.expect?.steps?.[0]?.author || payload?.meta.turns?.[0]?.author || selection.caseId;
  const focus = payload?.expect?.steps?.[0]?.focus || payload?.meta.turns?.[0]?.focus || "";

  const currentAttr = attributions.find(
    item => item.case_id === selection.caseId && item.attempt === selection.attempt,
  ) || {
    case_id: selection.caseId,
    attempt: selection.attempt,
    category: payload?.default_attribution || "tool",
    note: "",
  };

  function selectSpan(spanId: string) {
    setActive(spanId);
    setActiveCheck(null);
  }

  function openCheck(checkId: string) {
    const check = checks.find(item => item.id === checkId);
    setActiveCheck(checkId);
    if (!check) return;
    const span = firstSpanRef(check);
    if (span?.span_id) setActive(span.span_id);
    else if (hasFileRef(check)) setTab("diff");
  }

  const humanChecks = checks.filter(item => item.level === "human");

  return (
    <div className="wb-layout">
      <aside className="wb-panel">
        <h2>案例</h2>
        <div className="wb-cases">
          {(report.cases || [])
            .filter(caseItem => {
              if (filter === "all") return true;
              return (caseItem.attempts || []).some(row =>
                filter === "fail"
                  ? row.outcome === "fail" || caseItem.must_failed
                  : row.outcome === "pending_review",
              );
            })
            .map(caseItem => (
              <div
                key={caseItem.case_id}
                className={`wb-case ${caseItem.case_id === selection.caseId ? "active" : ""}`}
                onClick={() => {
                  const first = caseItem.attempts?.[0];
                  if (first) onSelect({ caseId: caseItem.case_id, attempt: first.attempt });
                }}
              >
                <b>{caseItem.case_id}</b>
                <div className="wb-dots">
                  {(caseItem.attempts || []).map(row => (
                    <button
                      key={row.attempt}
                      type="button"
                      className="wb-dot"
                      title={`#${row.attempt} ${row.outcome || row.status}`}
                      style={{ background: outcomeColor(row.status, row.outcome) }}
                      onClick={event => {
                        event.stopPropagation();
                        onSelect({ caseId: caseItem.case_id, attempt: row.attempt });
                      }}
                    />
                  ))}
                </div>
              </div>
            ))}
        </div>
        <div className="wb-filters">
          {(["all", "fail", "pending_review"] as Filter[]).map(item => (
            <button key={item} type="button" className={filter === item ? "on" : ""} onClick={() => onFilter(item)}>
              {item === "all" ? "全部" : item === "fail" ? "失败" : "待复核"}
            </button>
          ))}
        </div>
      </aside>

      <main className="wb-panel wb-center">
        <div className="wb-ask">
          <b>{ask}</b>
          <span>{focus}{payload?.row.model_requests != null ? ` · ${payload.row.model_requests} 次模型请求` : ""}</span>
        </div>
        <div className="wb-stages">
          {(["scope", "read", "candidate", "verify", "repair", "commit"] as const).map(id => {
            const stage = stages.find(item => item.id === id) || { id, verdict: "not_reached" };
            return (
              <button
                key={id}
                type="button"
                className={`wb-stage ${firstFailed === id ? "first" : ""}`}
                data-v={stage.verdict}
                onClick={() => stage.span_id && selectSpan(String(stage.span_id))}
              >
                <b>{payload?.stage_labels?.[id] || id}</b>
                {VERDICT[stage.verdict] || stage.verdict}
              </button>
            );
          })}
        </div>
        <div className="wb-trace-wrap">
          {loadError && <p className="wb-error">{loadError}</p>}
          <TraceTimelineView
            trace={trace}
            loading={!trace && !loadError}
            marks={marks}
            missing={missing}
            activeSpanId={active}
            onSelect={selectSpan}
          />
        </div>
        <div className="wb-tabs">
          <div className="wb-tabbar">
            {([
              ["checks", "检查项"],
              ["diff", "正文差异"],
              ["review", "人工复核"],
              ...(payload?.row.outcome === "fail" || payload?.row.outcome === "pending_review"
                ? [["attr", "归因"] as const]
                : []),
            ] as [Tab, string][]).map(([key, label]) => (
              <button key={key} type="button" className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </div>
          <div className="wb-tabbody">
            {tab === "checks" && (
              <table>
                <thead>
                  <tr><th>视角</th><th>检查项</th><th>级别</th><th>结论</th><th>原因</th></tr>
                </thead>
                <tbody>
                  {checks.map(check => (
                    <tr key={check.id} className="click" onClick={() => openCheck(check.id)}>
                      <td><span className="wb-lens">{checkLens(check)}</span></td>
                      <td><code>{check.id}</code></td>
                      <td>{check.level}</td>
                      <td className="wb-verdict" style={{ color: COLOR[check.verdict] }}>{VERDICT[check.verdict] || check.verdict}</td>
                      <td>{check.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {tab === "diff" && (
              diff ? (
                <>
                  {diff.unchanged && <div className="wb-banner">锚点区域内文本与初态相同：本轮没有改动正文。</div>}
                  <div className="wb-diff">
                    <pre dangerouslySetInnerHTML={{ __html: highlightRegion(diff.before, diff.region.from, diff.region.to, diff.preserve) }} />
                    <pre dangerouslySetInnerHTML={{ __html: highlightRegion(diff.after, diff.region.from, diff.region.to, diff.preserve) }} />
                  </div>
                </>
              ) : <p className="wb-muted">无差异证据。</p>
            )}
            {tab === "review" && (
              <ReviewPane
                caseId={selection.caseId}
                attempt={selection.attempt}
                checks={humanChecks}
                reviews={reviews}
                diff={diff}
                reply={trace?.events.find(item => item.kind === "reply")?.summary || ""}
                onChange={onReviewsChange}
                onSave={() => onSaveReviews(reviews, attributions)}
                status={status}
              />
            )}
            {tab === "attr" && (
              <AttributionPane
                options={payload?.attribution_options || []}
                value={currentAttr}
                firstFailed={firstFailed}
                stageLabels={payload?.stage_labels || {}}
                onChange={next => {
                  const others = attributions.filter(
                    item => !(item.case_id === next.case_id && item.attempt === next.attempt),
                  );
                  onAttributionsChange([...others, next]);
                }}
                onSave={() => onSaveReviews(reviews, attributions)}
                status={status}
              />
            )}
          </div>
        </div>
      </main>

      <aside className="wb-panel">
        <div className="wb-detail">
          {activeCheck ? (
            <CheckDetail checks={checks} checkId={activeCheck} onJump={selectSpan} onDiff={() => setTab("diff")} />
          ) : detailItem ? (
            <TraceDetailView key={detailItem.span_id} item={detailItem} loader={loader} />
          ) : missing.some(item => item.id === active) ? (
            <div className="wb-box">
              <h4>预期未出现</h4>
              <p>{missing.find(item => item.id === active)?.expected}</p>
            </div>
          ) : (
            <p className="wb-muted">选中时间线一步或检查项查看详情。</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function CheckDetail({
  checks,
  checkId,
  onJump,
  onDiff,
}: {
  checks: AttemptPayload["row"]["checks"];
  checkId: string;
  onJump: (spanId: string) => void;
  onDiff: () => void;
}) {
  const check = checks.find(item => item.id === checkId);
  if (!check) return null;
  return (
    <>
      <div><span className="wb-lens">{checkLens(check)}</span> <b>{check.id}</b></div>
      <div className="wb-box">
        <h4>结论</h4>
        <span className="wb-verdict" style={{ color: COLOR[check.verdict] }}>
          {VERDICT[check.verdict] || check.verdict}
        </span>
        {" · "}{check.level}
      </div>
      <div className="wb-box"><h4>判定原因</h4>{check.reason}</div>
      <div className="wb-box">
        <h4>证据</h4>
        {(check.evidence || []).map((ref, index) => {
          if (ref.type === "span" && ref.span_id) {
            return (
              <span key={index} className="wb-ref" onClick={() => onJump(ref.span_id!)} role="button">
                span · {ref.span_id}
              </span>
            );
          }
          if (ref.type === "missing") {
            return <span key={index} className="wb-ref">missing · {ref.expected}</span>;
          }
          if (ref.type === "file" || ref.type === "note") {
            return (
              <span key={index} className="wb-ref" onClick={onDiff} role="button">
                {ref.type} · {ref.path || ref.text || "正文差异"}
              </span>
            );
          }
          return <span key={index} className="wb-ref">{ref.type} · {ref.operation_id || JSON.stringify(ref)}</span>;
        })}
      </div>
    </>
  );
}

function ReviewPane({
  caseId,
  attempt,
  checks,
  reviews,
  diff,
  reply,
  onChange,
  onSave,
  status,
}: {
  caseId: string;
  attempt: number;
  checks: AttemptPayload["row"]["checks"];
  reviews: Review[];
  diff: DiffView | null;
  reply: string;
  onChange: (reviews: Review[]) => void;
  onSave: () => void;
  status: string;
}) {
  if (!checks.length) return <p className="wb-muted">没有人工复核项。</p>;

  function upsert(checkId: string, patch: Partial<Review>) {
    const existing = reviews.find(
      item => item.case_id === caseId && item.attempt === attempt && item.check_id === checkId,
    );
    const next: Review = {
      case_id: caseId,
      attempt,
      check_id: checkId,
      verdict: existing?.verdict || "pending_review",
      evidence_ref: existing?.evidence_ref || "",
      reason: existing?.reason || "",
      ...patch,
    };
    onChange([
      ...reviews.filter(
        item => !(item.case_id === caseId && item.attempt === attempt && item.check_id === checkId),
      ),
      next,
    ]);
  }

  return (
    <>
      {checks.map(check => {
        const current = reviews.find(
          item => item.case_id === caseId && item.attempt === attempt && item.check_id === check.id,
        );
        return (
          <div key={check.id} className="wb-review" style={{ marginBottom: 12 }}>
            <div>
              <b className="mono">{check.id}</b>
              <p>{check.reason}</p>
              <div className="wb-opts">
                {([["pass", "是"], ["fail", "否"], ["pending_review", "不确定"]] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={(current?.verdict || "pending_review") === value ? "on" : ""}
                    onClick={() => upsert(check.id, { verdict: value })}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <input
                className="wb-input"
                placeholder="点右侧段落填入证据位置"
                value={current?.evidence_ref || ""}
                onChange={event => upsert(check.id, { evidence_ref: event.target.value })}
              />
            </div>
            <div>
              {diff && (
                <pre
                  className="wb-diff"
                  style={{ display: "block", cursor: "pointer" }}
                  onClick={() =>
                    upsert(check.id, {
                      evidence_ref: `after:${diff.path} ¶${diff.region.from || ""}`.trim(),
                    })
                  }
                  dangerouslySetInnerHTML={{
                    __html: highlightRegion(diff.after, diff.region.from, diff.region.to, diff.preserve),
                  }}
                />
              )}
              {reply && <pre style={{ background: "#f6fbfa", marginTop: 8 }}>{reply}</pre>}
            </div>
          </div>
        );
      })}
      <button type="button" className="wb-chip btn" onClick={onSave}>保存并合并</button>
      {status && <p className="wb-muted">{status}</p>}
    </>
  );
}

function AttributionPane({
  options,
  value,
  firstFailed,
  stageLabels,
  onChange,
  onSave,
  status,
}: {
  options: { id: string; label: string; hint: string }[];
  value: Attribution;
  firstFailed?: string | null;
  stageLabels: Record<string, string>;
  onChange: (value: Attribution) => void;
  onSave: () => void;
  status: string;
}) {
  return (
    <>
      <div className="wb-attr">
        {options.map(option => (
          <button
            key={option.id}
            type="button"
            className={value.category === option.id ? "on" : ""}
            onClick={() => onChange({ ...value, category: option.id })}
          >
            <b>{option.label}</b>
            <small>{option.hint}</small>
          </button>
        ))}
      </div>
      <p className="wb-muted" style={{ margin: ".6rem 0" }}>
        最早失败环节：{firstFailed ? (stageLabels[firstFailed] || firstFailed) : "无"}
      </p>
      <input
        className="wb-input"
        placeholder="一句说明"
        value={value.note || ""}
        onChange={event => onChange({ ...value, note: event.target.value })}
      />
      <div style={{ marginTop: 8 }}>
        <button type="button" className="wb-chip btn" onClick={onSave}>保存并合并</button>
      </div>
      {status && <p className="wb-muted">{status}</p>}
    </>
  );
}
