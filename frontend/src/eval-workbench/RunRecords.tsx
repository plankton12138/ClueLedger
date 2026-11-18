import { useEffect, useLayoutEffect, useState } from "react";

import { cancelJob, fetchJobs, fetchRuns } from "./api";
import type { CheckTarget, JobPayload, RunSummary } from "./api";
import { caseName, checkLabels, jobIsActive, jobStatusLabel, modeShort, runDate } from "./labels";
import { routeHash, type RecordFilter, type ReportFocus, type Route } from "./routing";

type RecordsRoute = Extract<Route, { page: "reports" }>;

const FILTERS = [
  ["all", "全部"],
  ["review", "待我确认"],
  ["issues", "需排查"],
] as const;

const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

function titleFor(caseIds: string[] | undefined, fallback: string) {
  if (caseIds?.length === 1) return caseName(caseIds[0]);
  return fallback;
}

export function RunRecords({ filter, createdJob, scrollPositions }: {
  filter: RecordFilter;
  createdJob: JobPayload | null;
  scrollPositions: Partial<Record<RecordFilter, number>>;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [jobs, setJobs] = useState<JobPayload[]>(createdJob ? [createdJob] : []);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function poll() {
      try {
        const tasks = await fetchJobs();
        const records = await fetchRuns();
        if (!active) return;
        setRuns(records.runs);
        setJobs(tasks.jobs);
        setError("");
        if (tasks.jobs.some(jobIsActive)) timer = window.setTimeout(poll, 1500);
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
          timer = window.setTimeout(poll, 3000);
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [refresh]);

  useLayoutEffect(() => {
    if (loading) return;
    window.scrollTo(0, scrollPositions[filter] || 0);
    const rememberScroll = () => { scrollPositions[filter] = window.scrollY; };
    window.addEventListener("scroll", rememberScroll, { passive: true });
    return () => { window.removeEventListener("scroll", rememberScroll); };
  }, [filter, loading, scrollPositions]);

  async function stop(job: JobPayload) {
    setCancellingId(job.id);
    try {
      const next = await cancelJob(job.id);
      setJobs(items => items.map(item => item.id === job.id ? next : item));
      setRefresh(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCancellingId(null);
    }
  }

  const jobsByRun = new Map(jobs.map(job => [job.run_id, job]));
  const runsById = new Map(runs.map(run => [run.id, run]));
  const queue = jobs.filter(job => jobIsActive(job) || !runsById.has(job.run_id));
  const records = runs.filter(run => {
    const job = jobsByRun.get(run.id);
    return !job || !jobIsActive(job);
  }).map(run => {
    const job = jobsByRun.get(run.id);
    const started = runDate(run.id, run.identity?.started_at ?? job?.created_at);
    return { run, job, started, time: started?.getTime() ?? (run.mtime || 0) * 1000 };
  }).sort((a, b) => b.time - a.time);
  const pending = records.reduce((sum, { run }) => sum + (run.check_summary?.pending || 0) +
    (run.check_summary?.insufficient || 0), 0);
  const visible = records.filter(({ run, job }) => filter === "all" ||
    (filter === "review" ? !!run.review_target : !!run.issue_target || job?.status === "failed"));
  const visibleJobs = queue.filter(job => filter === "all" || (filter === "issues" && job.status === "failed"));

  function open(run: RunSummary, target?: CheckTarget | null, focus?: ReportFocus) {
    const route: RecordsRoute = {
      page: "reports", runId: run.id, filter,
      preferCaseId: target?.caseId, preferAttempt: target?.attempt, preferCheckId: target?.checkId, focus,
    };
    return routeHash(route);
  }

  return (
    <main className="rr-page rr-wide">
      <div className="rr-title rr-heading-row">
        <h1>评测记录</h1>
        <a className="rr-primary" href={routeHash({ page: "run", presetCaseIds: [], suiteId: null })}>开始评测</a>
      </div>
      <div className="rr-record-toolbar">
        <nav className="rr-tabs" aria-label="筛选记录">
          {FILTERS.map(([value, label]) => (
            <a key={value} aria-current={filter === value ? "page" : undefined}
              href={routeHash({ page: "reports", runId: null, filter: value })}>
              {label}{value === "review" && pending > 0 ? ` ${pending}` : ""}
            </a>
          ))}
        </nav>
        <button type="button" className="rr-link" onClick={() => setRefresh(value => value + 1)}>刷新</button>
      </div>
      {error && <p className="wb-error" role="alert">{error}</p>}
      {loading && !visibleJobs.length && <p className="rr-meta" role="status">正在读取评测记录…</p>}
      {!loading && !error && !visible.length && !visibleJobs.length && (
        <p className="rr-empty">{filter === "review" ? "没有待复核的检查项。" :
          filter === "issues" ? "没有需要排查的记录。" : "还没有评测记录。"}</p>
      )}
      {visibleJobs.map(job => (
        <article className="rr-record-row" key={job.id}>
          <div className="rr-record-main">
            <h2>{titleFor(job.case_ids, job.suite_id || job.run_id)}</h2>
            <div className="rr-meta">{dateFormat.format(new Date(job.created_at))} · {modeShort(job.mode)}</div>
            <p role="status">{jobStatusLabel(job.status)}
              {jobIsActive(job) && job.total_attempts > 0 && ` · ${job.completed_attempts}/${job.total_attempts} 次试验`}
              {job.current_case && jobIsActive(job) && ` · ${caseName(job.current_case)}`}
            </p>
            {job.error && <p className="wb-error">{job.error}</p>}
            {job.log.length > 0 && <details><summary>运行日志</summary><pre>{job.log.join("\n")}</pre></details>}
          </div>
          {jobIsActive(job) && (
            <button className="rr-link" type="button" disabled={job.status === "cancelling" || cancellingId === job.id}
              onClick={() => void stop(job)}>取消运行</button>
          )}
        </article>
      ))}
      {visible.map(({ run, job, started, time }) => {
        const labels = checkLabels(run.check_summary);
        const review = filter !== "issues" && run.review_target;
        return (
          <article className="rr-record-row" key={run.id}>
            <div className="rr-record-main">
              <h2><a href={open(run)}>{titleFor(run.case_ids, run.suite_id || run.id)}</a></h2>
              <div className="rr-meta">
                {time > 0 && `${started ? "" : "更新于 "}${dateFormat.format(time)} · `}{modeShort(run.mode)}
              </div>
              <div className="rr-record-facts">
                {labels.map(item => <span className={`rr-${item.tone}`} key={item.text}>{item.text}</span>)}
                {job && ["failed", "cancelled"].includes(job.status) && <span>{jobStatusLabel(job.status)}</span>}
                {!labels.length && (!job || job.status === "completed") && <span className="rr-meta">已完成</span>}
              </div>
              {job?.error && <p className="wb-error">{job.error}</p>}
            </div>
            <div className="rr-record-actions">
              {review && <a className="rr-link" href={open(run, review, "review")}>继续复核 →</a>}
              {run.issue_target && <a className="rr-link" href={open(run, run.issue_target, "checks")}>查看问题 →</a>}
              {!review && !run.issue_target && <a className="rr-link" href={open(run)}>查看报告 →</a>}
            </div>
          </article>
        );
      })}
    </main>
  );
}
