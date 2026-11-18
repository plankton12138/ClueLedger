import { useEffect, useState } from "react";

import {
  createJob, fetchCases, fetchEnvs, fetchJobs, fetchRuns, fetchSuites,
  type CaseSummary, type EnvSummary, type JobPayload, type RunSummary, type SuiteSummary,
} from "./api";
import { caseName, jobIsActive, modeShort } from "./labels";
import { routeHash } from "./routing";

export function LaunchPage({ presetCaseIds, presetSuiteId, onStarted }: {
  presetCaseIds: string[];
  presetSuiteId: string | null;
  onStarted: (job: JobPayload) => void;
}) {
  const [suites, setSuites] = useState<SuiteSummary[]>([]);
  const [envs, setEnvs] = useState<EnvSummary[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [suiteId, setSuiteId] = useState(presetSuiteId || "");
  const [caseIds, setCaseIds] = useState(presetCaseIds);
  const [envId, setEnvId] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8000");
  const [maxRequests, setMaxRequests] = useState("");
  const [outName, setOutName] = useState("");
  const [baseline, setBaseline] = useState("");
  const [activeJob, setActiveJob] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.all([fetchSuites(), fetchEnvs(), fetchCases(), fetchRuns(), fetchJobs()])
      .then(([s, e, c, r, j]) => {
        if (!active) return;
        setSuites(s.suites);
        setEnvs(e.envs);
        setCases(c.cases);
        setRuns(r.runs);
        setSuiteId(presetSuiteId || (presetCaseIds.length ? "" : (s.suites.find(item => item.mode === "smoke") || s.suites[0])?.id || ""));
        setCaseIds(presetCaseIds);
        setActiveJob(j.jobs.some(jobIsActive));
        setLoading(false);
      }, reason => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [presetSuiteId, presetCaseIds]);

  useEffect(() => {
    if (!activeJob) return;
    let mounted = true;
    const timer = window.setInterval(() => {
      void fetchJobs().then(({ jobs }) => {
        if (mounted) setActiveJob(jobs.some(jobIsActive));
      }, () => {});
    }, 2000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [activeJob]);

  const suite = suites.find(item => item.id === suiteId);
  const env = envs.find(item => item.id === (envId || suite?.default_env || envs[0]?.id));
  const selected = suite?.cases || caseIds;
  const mode = suite?.mode === "gate" ? "gate" : "smoke";
  const repeat = suite?.repeat || 1;
  const canStart = !loading && !activeJob && env && selected.length > 0 && (!suiteId || suite);

  async function start() {
    if (!canStart || busy) return;
    setError("");
    setBusy(true);
    try {
      const job = await createJob({
        case_ids: suite ? undefined : caseIds,
        suite_id: suite?.id,
        env_id: env.id,
        mode,
        repeat,
        base_url: baseUrl.trim(),
        max_requests: Number(maxRequests || env.budget),
        out_name: outName.trim() || undefined,
        baseline: baseline || undefined,
      });
      onStarted(job);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="rr-page rr-launch">
      <header className="rr-title"><h1>开始评测</h1></header>
      {error && <p className="wb-error" role="alert">{error}</p>}
      {loading ? <p className="rr-meta" role="status">正在读取评测配置…</p> : (
        <form onSubmit={event => { event.preventDefault(); void start(); }}>
          <fieldset disabled={busy} className="rr-launch-fields">
            <label>评测套件
              <select value={suiteId} onChange={event => {
                setSuiteId(event.target.value);
                setEnvId("");
                setMaxRequests("");
              }}>
                <option value="">手动选择案例</option>
                {suiteId && !suite && <option value={suiteId}>{suiteId}（找不到套件）</option>}
                {suites.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
            </label>
            {suite?.desc && <p className="rr-meta">{suite.desc}</p>}
            {!suiteId && (
              <fieldset className="rr-case-options">
                <legend>案例</legend>
                {cases.map(item => (
                  <label key={item.id}><input type="checkbox" checked={caseIds.includes(item.id)}
                    onChange={event => setCaseIds(previous => event.target.checked ?
                      [...previous, item.id] : previous.filter(id => id !== item.id))} />
                    {caseName(item.id)}
                  </label>
                ))}
                {!cases.length && <p className="rr-meta">没有可选案例。</p>}
              </fieldset>
            )}
            <label>运行环境
              <select value={env?.id || ""} onChange={event => {
                setEnvId(event.target.value);
                setMaxRequests("");
              }}>
                {!env && <option value="">请选择环境</option>}
                {envs.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
            </label>
            <div className="rr-launch-summary">
              <strong>{modeShort(mode)} · {selected.length} 个案例 · 每案 {repeat} 次</strong>
              {selected.length > 0 && <div>{selected.map(caseName).join("、")}</div>}
              <div className="rr-meta">检查包 · {suite?.default_evaluator || "使用各案例的检查规则"}</div>
            </div>
            <details className="rr-disclosure">
              <summary>高级选项</summary>
              <div className="rr-launch-fields rr-detail">
                <label>本地服务地址<input required value={baseUrl} onChange={event => setBaseUrl(event.target.value)} /></label>
                <label>模型请求上限<input type="number" min={1} placeholder={String(env?.budget || "")}
                  value={maxRequests} onChange={event => setMaxRequests(event.target.value)} /></label>
                <label>输出目录名<input value={outName} onChange={event => setOutName(event.target.value)} /></label>
                <label>对比运行
                  <select value={baseline} onChange={event => setBaseline(event.target.value)}>
                    <option value="">无</option>
                    {runs.map(run => <option key={run.id} value={run.id}>{run.id}</option>)}
                  </select>
                </label>
              </div>
            </details>
          </fieldset>
          <div className="rr-answer">
            <a className="rr-link" href={routeHash({ page: "reports", runId: null })}>
              {activeJob ? "查看运行进度" : "返回评测记录"}
            </a>
            <button className="rr-primary" type="submit" disabled={!canStart || busy}>
              {busy ? "正在提交…" : "开始评测"}
            </button>
          </div>
        </form>
      )}
    </main>
  );
}
