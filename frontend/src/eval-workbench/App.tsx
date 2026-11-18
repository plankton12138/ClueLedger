import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { TraceDetailView, TraceTimelineView } from "../components/trace";
import type { TraceEvent, TurnTrace } from "../components/trace";

import {
  type AttemptPayload,
  type Attribution,
  type CaseView,
  type CheckRow,
  type DiffView,
  type Report,
  type Review,
  type JobPayload,
  fetchAttempt,
  fetchDiff,
  fetchReport,
  fetchReviews,
  fetchTrace,
  mergeScore,
  saveReviews,
} from "./api";
import { AssetsPage } from "./AssetsPage";
import { evidenceTraceLoader } from "./evidenceLoader";
import { caseName, lensLabel, levelLabel, stageLabel } from "./labels";
import { LaunchPage } from "./LaunchPage";
import { checkLens, firstSpanRef, hasFileRef, marksFromChecks, missingFromChecks } from "./marks";
import { RunRecords } from "./RunRecords";
import { ReviewReport } from "./ReviewReport";
import { parseHash, routeHash, writeHash, type RecordFilter, type ReportFocus, type Route } from "./routing";
import { resolveTheme, toggleTheme, type WbTheme } from "./theme";

function ShellHeader({ onRecords, assets, theme, onToggleTheme }: {
  onRecords: string;
  assets: boolean;
  theme: WbTheme;
  onToggleTheme: () => void;
}) {
  return (
    <header className="rr-header">
      <span><b>线簿</b><span className="rr-brand-divider">评测工作台</span></span>
      <nav className="rr-tabs" aria-label="评测工作台导航">
        <a href={onRecords} aria-current={!assets ? "page" : undefined}>评测记录</a>
        <a href="#/assets" aria-current={assets ? "page" : undefined}>评测配置</a>
      </nav>
      <button type="button" className="rr-link" onClick={onToggleTheme}
        aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}>
        {theme === "dark" ? "浅色" : "深色"}
      </button>
    </header>
  );
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

type Tab = "checks" | "diff" | "attr" | "trace" | "reply";
type Selection = { caseId: string; attempt: number } | null;

const TAB_LABELS: Record<Tab, string> = {
  checks: "问题",
  diff: "正文差异",
  attr: "归因",
  trace: "运行轨迹",
  reply: "模型回复",
};

function isProblemAttempt(row: CaseView["attempts"][number], caseItem: CaseView) {
  return (
    row.outcome === "fail" ||
    row.outcome === "pending_review" ||
    caseItem.must_failed ||
    row.status === "infrastructure_error" ||
    row.status === "not_run"
  );
}

function pickPreferredSelection(
  report: Report,
  preferCaseId?: string | null,
  preferAttempt?: number | null,
): Selection {
  const attempts = (report.cases || []).find(item => item.case_id === preferCaseId)?.attempts
    || report.attempts?.filter(item => item.case_id === preferCaseId) || [];
  const picked = attempts.find(item => item.attempt === preferAttempt) || attempts[0];
  return picked ? { caseId: picked.case_id, attempt: picked.attempt } : null;
}

function representativeAttempt(caseItem: CaseView) {
  return (
    (caseItem.attempts || []).find(row => isProblemAttempt(row, caseItem)) ||
    caseItem.attempts?.[0] ||
    null
  );
}

function caseHealth(caseItem: CaseView): "fail" | "pending_review" | "error" | "pass" | "empty" {
  const rows = caseItem.attempts || [];
  if (!rows.length) return "empty";
  if (caseItem.must_failed || rows.some(row => row.outcome === "fail")) return "fail";
  if (rows.some(row => row.outcome === "pending_review")) return "pending_review";
  if (rows.some(row => row.status === "infrastructure_error" || row.status === "not_run")) {
    return "error";
  }
  return "pass";
}

const HEALTH_LABEL: Record<ReturnType<typeof caseHealth>, string> = {
  fail: "失败",
  pending_review: "待复核",
  error: "异常",
  pass: "通过",
  empty: "无记录",
};

const VERDICT_RANK: Record<string, number> = {
  fail: 0,
  pending_review: 1,
  skipped: 2,
  pass: 3,
};

const LEVEL_RANK: Record<string, number> = {
  must: 0,
  quality: 1,
  human: 2,
};

function sortChecks(checks: CheckRow[]): CheckRow[] {
  return [...checks].sort((a, b) => {
    const verdict = (VERDICT_RANK[a.verdict] ?? 9) - (VERDICT_RANK[b.verdict] ?? 9);
    if (verdict) return verdict;
    return (LEVEL_RANK[a.level] ?? 9) - (LEVEL_RANK[b.level] ?? 9);
  });
}

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
  const [route, setRoute] = useState<Route>(() => parseHash());
  const [theme, setTheme] = useState<WbTheme>(() => resolveTheme());
  const [createdJob, setCreatedJob] = useState<JobPayload | null>(null);
  const recordScroll = useRef<Partial<Record<RecordFilter, number>>>({});
  const lastFilter = useRef<RecordFilter>("all");
  const [report, setReport] = useState<Report | null>(null);
  const [reportRunId, setReportRunId] = useState<string | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [attributions, setAttributions] = useState<Attribution[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [focusTab, setFocusTab] = useState<ReportFocus | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [detailCheck, setDetailCheck] = useState<string>();
  const reportScroll = useRef<number | null>(null);
  const [busyRun, setBusyRun] = useState<string | null>(null);
  const [scoreRetry, setScoreRetry] = useState<{ runId: string; error: string } | null>(null);
  const busy = useRef(new Set<string>());

  const onToggleTheme = useCallback(() => {
    setTheme(current => toggleTheme(current));
  }, []);

  const runId = route.page === "reports" ? route.runId : null;
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
    reportScroll.current = null;
  }, [route.page, runId]);
  useLayoutEffect(() => {
    if (!selection && reportScroll.current != null) {
      window.scrollTo(0, reportScroll.current);
      reportScroll.current = null;
    }
  }, [selection]);
  const currentRun = useRef(runId);
  currentRun.current = runId;
  if (route.page === "reports") lastFilter.current = route.filter || "all";
  const recordsHref = routeHash({ page: "reports", runId: null, filter: lastFilter.current });

  const navigate = useCallback((next: Route) => writeHash(next), []);

  const reload = useCallback(async () => {
    if (!runId) return;
    setError("");
    try {
      const nextReport = await fetchReport(runId);
      const payload = await fetchReviews(runId);
      if (currentRun.current !== runId) return;
      setReport(nextReport);
      setReportRunId(runId);
      setReviews(payload.reviews || []);
      setAttributions(payload.attributions || []);
    } catch (reason) {
      if (currentRun.current === runId) setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [runId]);

  useEffect(() => {
    const syncHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", syncHash);
    if (!location.hash) writeHash({ page: "reports", runId: null });
    return () => {
      window.removeEventListener("hashchange", syncHash);
    };
  }, []);

  useEffect(() => {
    if (!runId) {
      setReport(null);
      setReportRunId(null);
      setSelection(null);
      return;
    }
    let cancelled = false;
    setError("");
    setStatus("");
    const preferCaseId = route.page === "reports" ? route.preferCaseId : null;
    const preferAttempt = route.page === "reports" ? route.preferAttempt : null;
    void (async () => {
      try {
        const nextReport = await fetchReport(runId);
        const payload = await fetchReviews(runId);
        if (cancelled) return;
        setReport(nextReport);
        setReportRunId(runId);
        setReviews(payload.reviews || []);
        setAttributions(payload.attributions || []);
        const preferred = preferCaseId
          ? pickPreferredSelection(nextReport, preferCaseId, preferAttempt)
          : null;
        const inline = route.page === "reports" && (route.focus === "review" || !!route.preferCheckId || route.focus === "checks");
        setSelection(inline ? null : preferred);
        setFocusTab(!inline && preferred && route.page === "reports" ? route.focus || null : null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, route]);

  async function refreshScore(id: string) {
    try {
      const scored = await mergeScore(id);
      if (currentRun.current === id) {
        setReport(scored);
        setScoreRetry(null);
      }
      return scored;
    } catch (reason) {
      if (currentRun.current === id) {
        setScoreRetry({ runId: id, error: reason instanceof Error ? reason.message : String(reason) });
      }
      return null;
    }
  }

  async function withRunWrite<T>(id: string, write: () => Promise<T>) {
    if (busy.current.has(id)) throw new Error("[EVAL_BUSY] 评测报告：正在保存本次运行。建议：稍后重试");
    busy.current.add(id);
    setBusyRun(id);
    try {
      return await write();
    } finally {
      busy.current.delete(id);
      setBusyRun(value => value === id ? null : value);
    }
  }

  async function persistAndScore(
    nextReviews: Review[],
    nextAttrs: Attribution[],
  ) {
    if (!runId) return;
    setStatus("");
    try {
      await withRunWrite(runId, async () => {
        const saved = await saveReviews(runId, { reviews: nextReviews, attributions: nextAttrs });
        if (currentRun.current === runId) {
          setReviews(saved.reviews);
          setAttributions(saved.attributions);
          setStatus("已保存");
        }
        return refreshScore(runId);
      });
    } catch (reason) {
      if (currentRun.current === runId) setStatus(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function saveQuestion(review: Review) {
    if (!runId) return;
    await withRunWrite(runId, async () => {
      const current = await fetchReviews(runId);
      const next = current.reviews.filter(item => item.case_id !== review.case_id ||
        item.attempt !== review.attempt || item.check_id !== review.check_id);
      const saved = await saveReviews(runId, { reviews: [...next, review], attributions: current.attributions });
      if (currentRun.current === runId) {
        setReviews(saved.reviews);
        setAttributions(saved.attributions);
      }
      await refreshScore(runId);
    });
  }

  const shell = (content: React.ReactNode) => (
    <div className="rr-shell">
      <ShellHeader onRecords={recordsHref} assets={route.page === "assets"} theme={theme} onToggleTheme={onToggleTheme} />
      {content}
    </div>
  );

  if (route.page === "assets") {
    return shell(
      <AssetsPage
        tab={route.tab}
        onTab={tab => navigate({ page: "assets", tab })}
        onLaunchSuite={id => navigate({ page: "run", presetCaseIds: [], suiteId: id })}
        onLaunchCase={id => navigate({ page: "run", presetCaseIds: [id], suiteId: null })}
      />
    );
  }

  if (route.page === "run") {
    return shell(
      <LaunchPage presetCaseIds={route.presetCaseIds} presetSuiteId={route.suiteId}
        onStarted={job => {
          setCreatedJob(job);
          navigate({ page: "reports", runId: null });
        }} />
    );
  }

  if (!runId) {
    return shell(<RunRecords filter={route.filter || "all"} createdJob={createdJob} scrollPositions={recordScroll.current} />);
  }

  const breadcrumb = (
    <div className="rr-breadcrumb rr-page">
      {selection ? (
        <button type="button" className="rr-link" onClick={() => setSelection(null)}>← 返回报告</button>
      ) : <a className="rr-link" href={recordsHref}>← 返回评测记录</a>}
      {!selection && <button type="button" className="rr-link" disabled={busyRun === runId} onClick={() => void reload()}>刷新报告</button>}
    </div>
  );

  if (error || !report || reportRunId !== runId) {
    return shell(<>{breadcrumb}<main className="rr-page">
      {error ? <p className="wb-error" role="alert">{error}</p> : <p role="status">正在读取报告…</p>}
    </main></>);
  }

  const targetAttempt = route.preferCaseId ? pickPreferredSelection(report, route.preferCaseId, route.preferAttempt) : null;
  const target = targetAttempt ? { ...targetAttempt, checkId: route.preferCheckId } : null;

  return shell(
    <>
      {breadcrumb}
      {scoreRetry?.runId === runId && (
        <div className="rr-score-status" role="status">
          <p>复核已保存，报告待更新。{scoreRetry.error}</p>
          <button type="button" className="rr-link" disabled={busyRun === runId}
            onClick={() => void withRunWrite(runId, () => refreshScore(runId))}>重试更新报告</button>
        </div>
      )}
      <div hidden={!!selection}>
        <ReviewReport key={runId} runId={runId} report={report} reviews={reviews} saving={busyRun === runId}
          onSave={saveQuestion} target={target}
          onDetail={(caseId, attempt, focus, checkId) => {
            reportScroll.current = window.scrollY;
            setDetailCheck(checkId);
            setSelection({ caseId, attempt });
            setFocusTab(focus);
            setStatus("");
          }} />
      </div>
      {selection && (
        <section className="rr-diagnostics">
          <header className="rr-title">
            <div className="rr-meta">{caseName(selection.caseId)} · 第 {selection.attempt} 次试验</div>
            <h1>检查证据</h1>
          </header>
          <AttemptView key={`${runId}:${selection.caseId}:${selection.attempt}`}
          runId={runId} report={report} selection={selection}
          onSelect={next => { setSelection(next); setFocusTab(null); setDetailCheck(undefined); }}
          reviews={reviews} attributions={attributions} status={status} initialTab={focusTab} initialCheck={detailCheck}
          onSaveReviews={(next, attrs) => void persistAndScore(next, attrs)} />
        </section>
      )}
    </>
  );
}

function AttemptView({
  runId,
  report,
  selection,
  onSelect,
  reviews: savedReviews,
  attributions: savedAttributions,
  status,
  initialTab,
  initialCheck,
  onSaveReviews,
}: {
  runId: string;
  report: Report;
  selection: { caseId: string; attempt: number };
  onSelect: (value: Selection) => void;
  reviews: Review[];
  attributions: Attribution[];
  status: string;
  initialTab?: ReportFocus | null;
  initialCheck?: string;
  onSaveReviews: (reviews: Review[], attributions: Attribution[]) => void;
}) {
  const reviews = savedReviews;
  const [attributions, onAttributionsChange] = useState(savedAttributions);
  useEffect(() => { onAttributionsChange(savedAttributions); }, [savedAttributions]);
  const [payload, setPayload] = useState<AttemptPayload | null>(null);
  const [trace, setTrace] = useState<TurnTrace>();
  const [diff, setDiff] = useState<DiffView | null>(null);
  const [active, setActive] = useState("");
  const [activeCheck, setActiveCheck] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(() => initialTab && initialTab !== "review" ? initialTab : "checks");
  const [traceRequested, setTraceRequested] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    if (initialTab && initialTab !== "review") setTab(initialTab);
  }, [initialTab]);

  useEffect(() => {
    let alive = true;
    setLoadError("");
    setTrace(undefined);
    setTraceRequested(false);
    setDiff(null);
    setActive("");
    setActiveCheck(initialCheck || null);
    void (async () => {
      try {
        const next = await fetchAttempt(runId, selection.caseId, selection.attempt);
        if (!alive) return;
        setPayload(next);
        if (next.has_evidence) {
          const nextDiff = await fetchDiff(runId, selection.caseId, selection.attempt);
          if (alive) setDiff(nextDiff);
        }
      } catch (reason) {
        if (alive) setLoadError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => { alive = false; };
  }, [runId, selection.caseId, selection.attempt, initialCheck]);

  const turnId = payload?.turns[0]?.turn_id || payload?.meta.turns?.[0]?.turn_id || "";
  useEffect(() => {
    if (!traceRequested || !turnId) return;
    let alive = true;
    setLoadError("");
    void (async () => {
      try {
        const nextTrace = await fetchTrace(runId, selection.caseId, selection.attempt, turnId);
        if (alive) setTrace(nextTrace);
      } catch (reason) {
        if (alive) setLoadError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => { alive = false; };
  }, [traceRequested, runId, selection.caseId, selection.attempt, turnId]);

  const loader = useMemo(
    () => evidenceTraceLoader(runId, selection.caseId, selection.attempt),
    [runId, selection.caseId, selection.attempt],
  );
  const rawChecks = useMemo(() => payload?.row.checks || [], [payload]);
  const checks = useMemo(
    () =>
      rawChecks.map(check => {
        if (check.level !== "human") return check;
        const review = reviews.find(
          item =>
            item.case_id === selection.caseId &&
            item.attempt === selection.attempt &&
            item.check_id === check.id,
        );
        if (
          review?.verdict === "pass" ||
          review?.verdict === "fail" ||
          review?.verdict === "pending_review"
        ) {
          return { ...check, verdict: review.verdict };
        }
        return check;
      }),
    [rawChecks, reviews, selection.caseId, selection.attempt],
  );
  const orderedChecks = useMemo(() => sortChecks(checks), [checks]);
  const marks = useMemo(() => marksFromChecks(checks), [checks]);
  const missing = useMemo(() => missingFromChecks(checks), [checks]);
  const failedChecks = orderedChecks.filter(item => item.verdict === "fail");
  const pendingChecks = orderedChecks.filter(item => item.verdict === "pending_review");
  const hasTurns = Boolean(payload?.turns?.length || payload?.meta.turns?.length);
  const availableTabs = useMemo<Tab[]>(() => {
    const list: Tab[] = ["checks"];
    if (payload?.has_evidence || diff) list.push("diff");
    if (payload?.row.outcome === "fail" || payload?.row.outcome === "pending_review") {
      list.push("attr");
    }
    if (hasTurns) list.push("trace", "reply");
    return list;
  }, [payload, diff, hasTurns]);

  useEffect(() => {
    if (payload && !availableTabs.includes(tab)) setTab("checks");
  }, [payload, availableTabs, tab]);

  const detailItem = trace?.events.find((item: TraceEvent) => item.span_id === active);
  const missingItem = missing.find(item => item.id === active);
  const stages = payload?.row.stages || [];
  const firstFailed = payload?.row.first_failed_stage;
  const ask = payload?.expect?.steps?.[0]?.author || payload?.meta.turns?.[0]?.author || selection.caseId;
  const focus = payload?.expect?.steps?.[0]?.focus || payload?.meta.turns?.[0]?.focus || "";
  const replyText = trace?.events.find(item => item.kind === "reply")?.summary || "";

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
    setTraceRequested(true);
    setTab("trace");
  }

  function openCheck(checkId: string) {
    const check = checks.find(item => item.id === checkId);
    const span = check ? firstSpanRef(check) : null;
    if (span?.span_id) {
      setActiveCheck(checkId);
      setActive(span.span_id);
      return;
    }
    setActiveCheck(checkId);
    if (check && hasFileRef(check)) setTab("diff");
  }

  function toggleCheck(checkId: string) {
    if (activeCheck === checkId) {
      setActiveCheck(null);
      return;
    }
    openCheck(checkId);
  }

  function showTab(next: Tab) {
    setTab(next);
    if (next === "trace" || next === "reply") setTraceRequested(true);
  }

  const traceDetail = detailItem ? (
    <TraceDetailView key={detailItem.span_id} item={detailItem} loader={loader} />
  ) : missingItem ? (
    <div className="wb-box">
      <h4>预期未出现</h4>
      <p>{missingItem.expected}</p>
    </div>
  ) : null;

  return (
    <div className="wb-layout wb-diagnosis">
      <aside className="wb-panel wb-switcher">
        <h2>案例</h2>
        <div className="wb-cases">
          {(report.cases || []).map(caseItem => {
            const health = caseHealth(caseItem);
            const activeCase = caseItem.case_id === selection.caseId;
            return (
              <div
                key={caseItem.case_id}
                className={`wb-case ${activeCase ? "active" : ""}`}
                onClick={() => {
                  const rep = representativeAttempt(caseItem);
                  if (rep) onSelect({ caseId: caseItem.case_id, attempt: rep.attempt });
                }}
              >
                <b>{caseName(caseItem.case_id)}</b>
                <small className={`wb-case-status ${health}`}>{HEALTH_LABEL[health]}</small>
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
            );
          })}
        </div>
      </aside>

      <main className="wb-panel wb-center">
        <div className="wb-ask">
          <div className="wb-ask-main">
            <b>{ask}</b>
            <span>
              {selection.caseId} · 第 {selection.attempt} 次
              {focus ? ` · ${focus}` : ""}
              {payload?.row.model_requests != null ? ` · ${payload.row.model_requests} 次模型请求` : ""}
            </span>
          </div>
          {payload?.row && (
            <span
              className="wb-chip"
              style={{ color: outcomeColor(payload.row.status, payload.row.outcome) }}
            >
              {payload.row.status === "infrastructure_error" || payload.row.status === "not_run"
                ? payload.row.status
                : VERDICT[payload.row.outcome || ""] || payload.row.outcome || "—"}
            </span>
          )}
        </div>
        <div className="wb-stages">
          {!payload ? (
            <div className="wb-stage" data-v="skipped">
              <b>过程</b>
              读取中…
            </div>
          ) : stages.length ? (
            stages.map(stage => {
              const verdict = stage.verdict || "not_reached";
              return (
                <button
                  key={stage.id}
                  type="button"
                  className={`wb-stage ${firstFailed === stage.id ? "first" : ""}`}
                  data-v={verdict}
                  onClick={() => stage.span_id && selectSpan(String(stage.span_id))}
                >
                  <b>{payload.stage_labels?.[stage.id] || stageLabel(stage.id)}</b>
                  {VERDICT[verdict] || verdict}
                </button>
              );
            })
          ) : (
            <div className="wb-stage" data-v="skipped">
              <b>过程</b>
              无数据
            </div>
          )}
        </div>
        <div className="wb-issue-strip">
          <span>
            失败 {failedChecks.length} · 待复核 {pendingChecks.length}
            {firstFailed ? ` · 最早失败 ${payload?.stage_labels?.[firstFailed] || stageLabel(firstFailed)}` : ""}
          </span>
          {failedChecks[0] && (
            <button type="button" className="wb-chip ghost" onClick={() => openCheck(failedChecks[0].id)}>
              看第一个失败项
            </button>
          )}
          {availableTabs.includes("attr") && (
            <button type="button" className="wb-chip ghost" onClick={() => showTab("attr")}>
              去归因
            </button>
          )}
        </div>
        <div className="wb-tabs">
          <div className="wb-tabbar">
            {availableTabs.map(key => (
              <button key={key} type="button" className={tab === key ? "on" : ""} onClick={() => showTab(key)}>
                {TAB_LABELS[key]}
              </button>
            ))}
          </div>
          <div className={`wb-tabbody ${tab === "trace" ? "wb-tabbody-trace" : ""}`}>
            {loadError && <p className="wb-error">{loadError}</p>}
            {tab === "checks" && (
              <div className="wb-problem-list">
                {!orderedChecks.length ? (
                  <p className="wb-muted">没有检查项。</p>
                ) : (
                  orderedChecks.map(check => {
                    const open = activeCheck === check.id;
                    return (
                      <div key={check.id} className={`wb-problem ${open ? "open" : ""}`}>
                        <button type="button" className="wb-problem-row" onClick={() => toggleCheck(check.id)}>
                          <span className="wb-lens" title={lensLabel(checkLens(check))}>{lensLabel(checkLens(check))}</span>
                          <code>{check.id}</code>
                          <span className="wb-muted">{levelLabel(check.level)}</span>
                          <span className="wb-verdict" style={{ color: COLOR[check.verdict] }}>
                            {VERDICT[check.verdict] || check.verdict}
                          </span>
                          <span className="wb-problem-reason">{check.reason}</span>
                        </button>
                        {open && (
                          <div className="wb-problem-detail">
                            <CheckDetail
                              checks={checks}
                              checkId={check.id}
                              onJump={selectSpan}
                              onDiff={() => setTab("diff")}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
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
            {payload && tab === "attr" && (
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
            {tab === "trace" && (
              <div className="wb-trace-wrap wb-trace-pane">
                <TraceTimelineView
                  trace={trace}
                  loading={!trace && !loadError && traceRequested}
                  marks={marks}
                  missing={missing}
                  activeSpanId={active}
                  onSelect={selectSpan}
                  detail={traceDetail}
                />
              </div>
            )}
            {tab === "reply" && (
              trace ? (
                <pre className="wb-diff">{replyText || "该轮没有模型回复记录。"}</pre>
              ) : (
                <p className="wb-muted">{loadError ? loadError : "正在读取运行轨迹…"}</p>
              )
            )}
          </div>
        </div>
      </main>
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
      <div><span className="wb-lens">{lensLabel(checkLens(check))}</span> <b>{check.id}</b></div>
      <div className="wb-box">
        <h4>结论</h4>
        <span className="wb-verdict" style={{ color: COLOR[check.verdict] }}>
          {VERDICT[check.verdict] || check.verdict}
        </span>
        {" · "}{levelLabel(check.level)}
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
