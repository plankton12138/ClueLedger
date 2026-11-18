import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

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
import { AssetsPage } from "./AssetsPage";
import { evidenceTraceLoader } from "./evidenceLoader";
import { gateLabel, modeLabel, modeShort } from "./labels";
import { LaunchPage } from "./LaunchPage";
import { checkLens, firstSpanRef, hasFileRef, marksFromChecks, missingFromChecks } from "./marks";
import { QaPage } from "./QaPage";
import { parseHash, writeHash, type Route } from "./routing";
import { resolveTheme, toggleTheme, type WbTheme } from "./theme";

const PRODUCT_ATTR = new Set(["context", "model", "tool", "verify", "commit"]);

function ShellHeader({
  nav,
  theme,
  onToggleTheme,
  children,
}: {
  nav: ReactNode;
  theme: WbTheme;
  onToggleTheme: () => void;
  children?: ReactNode;
}) {
  return (
    <header className="wb-header">
      <h1>评测工作台</h1>
      {nav}
      {children}
      <span className="wb-header-spacer" />
      <button
        type="button"
        className="wb-chip ghost wb-theme-toggle"
        onClick={onToggleTheme}
        aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
        title={theme === "dark" ? "浅色" : "深色"}
      >
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

type Filter = "all" | "fail" | "pending_review";
type Tab = "checks" | "diff" | "review" | "attr";
type Selection = { caseId: string; attempt: number } | null;

function pickDefaultSelection(
  report: Report,
  preferCaseId?: string | null,
  preferAttempt?: number | null,
): Selection {
  const cases = report.cases || [];
  if (preferCaseId) {
    const matched = cases.find(item => item.case_id === preferCaseId);
    const picked =
      (preferAttempt != null &&
        matched?.attempts?.find(item => item.attempt === preferAttempt)) ||
      matched?.attempts?.[0];
    if (picked) return { caseId: preferCaseId, attempt: picked.attempt };
  }
  for (const caseItem of cases) {
    for (const row of caseItem.attempts || []) {
      if (
        row.outcome === "fail" ||
        row.outcome === "pending_review" ||
        caseItem.must_failed ||
        row.status === "infrastructure_error"
      ) {
        return { caseId: caseItem.case_id, attempt: row.attempt };
      }
    }
  }
  const firstCase = cases[0];
  const firstAttempt = firstCase?.attempts?.[0];
  if (firstCase && firstAttempt) {
    return { caseId: firstCase.case_id, attempt: firstAttempt.attempt };
  }
  return null;
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
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsRoot, setRunsRoot] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [attributions, setAttributions] = useState<Attribution[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selection, setSelection] = useState<Selection>(null);

  const onToggleTheme = useCallback(() => {
    setTheme(current => toggleTheme(current));
  }, []);

  const runId = route.page === "reports" ? route.runId : null;

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

  const navigate = useCallback((next: Route) => {
    setRoute(next);
    writeHash(next);
  }, []);

  const openRun = useCallback(
    (id: string | null, preferCaseId?: string | null, preferAttempt?: number | null) => {
      setError("");
      setReport(null);
      setReviews([]);
      setAttributions([]);
      setSelection(null);
      setStatus("");
      navigate({
        page: "reports",
        runId: id,
        preferCaseId: preferCaseId || null,
        preferAttempt: preferAttempt ?? null,
      });
      if (!id) void loadRuns();
    },
    [loadRuns, navigate],
  );

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
    const syncHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", syncHash);
    window.addEventListener("popstate", syncHash);
    if (!location.hash) writeHash({ page: "reports", runId: null });
    return () => {
      window.removeEventListener("hashchange", syncHash);
      window.removeEventListener("popstate", syncHash);
    };
  }, [loadRuns]);

  useEffect(() => {
    if (!runId) {
      setReport(null);
      setSelection(null);
      return;
    }
    let cancelled = false;
    setError("");
    const preferCaseId = route.page === "reports" ? route.preferCaseId : null;
    const preferAttempt = route.page === "reports" ? route.preferAttempt : null;
    void (async () => {
      try {
        const nextReport = await fetchReport(runId);
        const payload = await fetchReviews(runId);
        if (cancelled) return;
        setReport(nextReport);
        setReviews(payload.reviews || []);
        setAttributions(payload.attributions || []);
        setSelection(pickDefaultSelection(nextReport, preferCaseId, preferAttempt));
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, route]);

  async function persistAndScore(
    nextReviews: Review[],
    nextAttrs: Attribution[],
    opts?: { handoffAttribution?: boolean },
  ) {
    if (!runId) return;
    setStatus("");
    try {
      const saved = await saveReviews(runId, { reviews: nextReviews, attributions: nextAttrs });
      setReviews(saved.reviews);
      setAttributions(saved.attributions);
      const scored = await mergeScore(runId);
      setReport(scored);
      setStatus(`已合并；${gateLabel(scored.gate?.passed)}`);
      void loadRuns();
      if (opts?.handoffAttribution && selection) {
        const focus = nextAttrs.find(
          item => item.case_id === selection.caseId && item.attempt === selection.attempt,
        );
        if (focus?.category === "eval") {
          navigate({ page: "assets", tab: "evaluators" });
        } else if (focus?.category && PRODUCT_ATTR.has(focus.category)) {
          navigate({
            page: "run",
            presetCaseIds: [],
            suiteId: scored.suite_id || null,
          });
        }
      }
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const nav = (
    <nav className="wb-nav">
      <button
        type="button"
        className={`wb-chip ${route.page === "assets" ? "btn" : "ghost"}`}
        onClick={() => navigate({ page: "assets", tab: "suites" })}
      >
        资产
      </button>
      <button
        type="button"
        className={`wb-chip ${route.page === "run" ? "btn" : "ghost"}`}
        onClick={() => navigate({ page: "run", presetCaseIds: [], suiteId: null })}
      >
        开跑
      </button>
      <button
        type="button"
        className={`wb-chip ${route.page === "reports" ? "btn" : "ghost"}`}
        onClick={() => openRun(null)}
      >
        报告
      </button>
      <button
        type="button"
        className={`wb-chip ${route.page === "qa" ? "btn" : "ghost"}`}
        onClick={() => navigate({ page: "qa" })}
      >
        质检
      </button>
    </nav>
  );

  if (route.page === "assets") {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme} />
        <AssetsPage
          tab={route.tab}
          onTab={tab => navigate({ page: "assets", tab })}
          onLaunchSuite={id => navigate({ page: "run", presetCaseIds: [], suiteId: id })}
          onLaunchCase={id => navigate({ page: "run", presetCaseIds: [id], suiteId: null })}
        />
      </div>
    );
  }

  if (route.page === "qa") {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme} />
        <QaPage onOpenItem={(id, caseId, attempt) => openRun(id, caseId, attempt)} />
      </div>
    );
  }

  if (route.page === "run") {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme} />
        <LaunchPage
          presetCaseIds={route.presetCaseIds}
          presetSuiteId={route.suiteId}
          onOpenRun={id => openRun(id)}
        />
      </div>
    );
  }

  if (!runId) {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme}>
          <span className="wb-chip">本机已落盘运行</span>
          <button type="button" className="wb-chip ghost" onClick={() => void loadRuns()}>
            刷新列表
          </button>
        </ShellHeader>
        {error && <p className="wb-error" role="alert">{error}</p>}
        <RunPicker runs={runs} runsRoot={runsRoot} onOpen={id => openRun(id)} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme}>
          <button type="button" className="wb-chip ghost" onClick={() => openRun(null)}>
            ← 结果列表
          </button>
        </ShellHeader>
        <p className="wb-error" role="alert">{error}</p>
      </div>
    );
  }
  if (!report) {
    return (
      <div className="wb-root">
        <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme} />
        <p className="wb-muted" style={{ padding: "1rem" }}>
          正在读取报告…
        </p>
      </div>
    );
  }

  const identity = report.identity || {};
  const gate = report.gate || {};

  return (
    <div className="wb-root">
      <ShellHeader nav={nav} theme={theme} onToggleTheme={onToggleTheme}>
        <span className="wb-chip mono">{runId}</span>
        <span className="wb-chip">{modeLabel(report.mode)}</span>
        {(report.suite_id || report.env_id || report.evaluator_id) && (
          <span className="wb-chip" title="资产基线">
            {[report.suite_id, report.env_id, report.evaluator_id]
              .filter(Boolean)
              .join(" · ")}
            {report.evaluator_version != null ? ` v${report.evaluator_version}` : ""}
          </span>
        )}
        {identity.git_commit != null && (
          <span className="wb-chip mono">{String(identity.git_commit).slice(0, 8)}</span>
        )}
        {identity.model != null && <span className="wb-chip">{String(identity.model)}</span>}
        {identity.budget_used != null && (
          <span className="wb-chip">
            预算 {String(identity.budget_used)}
            {identity.budget_max != null ? ` / ${String(identity.budget_max)}` : ""}
          </span>
        )}
        <span className={`wb-chip ${gate.passed ? "ok" : "bad"}`}>{gateLabel(gate.passed)}</span>
        {report.baseline_compare && (
          <span className="wb-chip">
            对比上次{" "}
            {String((report.baseline_compare.baseline_identity || {}).git_commit || "").slice(0, 8) ||
              "已绑定"}
          </span>
        )}
        <button type="button" className="wb-chip ghost" onClick={() => openRun(null)}>
          ← 结果列表
        </button>
        <button type="button" className="wb-chip ghost" onClick={() => void reload()}>
          刷新
        </button>
      </ShellHeader>

      <ReportKpi report={report} />
      {selection ? (
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
          onSaveReviews={(next, attrs, handoff) =>
            void persistAndScore(next, attrs, { handoffAttribution: handoff })
          }
          onReviewsChange={setReviews}
          onAttributionsChange={setAttributions}
        />
      ) : (
        <p className="wb-muted" style={{ padding: "0 1rem 1rem" }}>
          本次运行没有可展开的案例试验。
        </p>
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
            <th>跑法</th>
            <th>资产</th>
            <th>验收</th>
            <th>摘要</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(item => {
            const summary = item.summary || {};
            const when = item.mtime ? new Date(item.mtime * 1000).toLocaleString() : "—";
            const assets = [item.suite_id, item.env_id, item.evaluator_id].filter(Boolean).join(" · ");
            return (
              <tr key={item.id} className="click" onClick={() => onOpen(item.id)} style={{ cursor: "pointer" }}>
                <td><code>{item.id}</code></td>
                <td>{modeShort(item.mode)}</td>
                <td className="wb-muted">{assets || "—"}</td>
                <td className={item.gate_passed ? "wb-delta better" : "wb-delta worse"}>
                  {gateLabel(item.gate_passed)}
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

function ReportKpi({ report }: { report: Report }) {
  const summary = report.summary || {};
  const pass = summary.pass || 0;
  const fail = summary.fail || 0;
  const pending = summary.pending_review || 0;
  const total = pass + fail + pending || 0;
  const blockers = (report.gate?.blockers || []).length;
  const deltaWorse = (report.baseline_compare?.cases || []).filter(item => item.delta === "worse").length;
  const deltaBetter = (report.baseline_compare?.cases || []).filter(item => item.delta === "better").length;

  return (
    <div className="wb-overview" style={{ paddingBottom: 0 }}>
      <div className="wb-kpi-grid">
        <div className="wb-kpi">
          <div className="label">是否通过</div>
          <strong className={report.gate?.passed ? "wb-delta better" : "wb-delta worse"}>
            {gateLabel(report.gate?.passed)}
          </strong>
        </div>
        <div className="wb-kpi">
          <div className="label">通过数</div>
          <strong>
            {pass}/{total || "—"}
          </strong>
        </div>
        <div className="wb-kpi">
          <div className="label">未过的必须项</div>
          <strong>{blockers}</strong>
        </div>
        <div className="wb-kpi">
          <div className="label">相对上次</div>
          <strong>
            {report.baseline_compare ? `变好 ${deltaBetter} · 变差 ${deltaWorse}` : "—"}
          </strong>
        </div>
      </div>
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
  onSaveReviews: (reviews: Review[], attributions: Attribution[], handoffAttribution?: boolean) => void;
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
                onSave={() => onSaveReviews(reviews, attributions, false)}
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
                onSave={() => onSaveReviews(reviews, attributions, true)}
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
            <div className="wb-review-doc">
              {diff && (
                <pre
                  className="wb-diff"
                  style={{ cursor: "pointer" }}
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
              {reply && <pre className="wb-diff" style={{ marginTop: 8 }}>{reply}</pre>}
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
