import { useEffect, useMemo, useState } from "react";

import {
  type EnvSummary,
  type JobPayload,
  type RunSummary,
  type SuiteSummary,
  cancelJob,
  createJob,
  fetchEnvs,
  fetchJob,
  fetchJobs,
  fetchRuns,
  fetchSuites,
} from "./api";
import { modeLabel } from "./labels";

export function LaunchPage({
  presetCaseIds,
  presetSuiteId,
  onOpenRun,
}: {
  presetCaseIds: string[];
  presetSuiteId: string | null;
  onOpenRun: (runId: string) => void;
}) {
  const [suites, setSuites] = useState<SuiteSummary[]>([]);
  const [envs, setEnvs] = useState<EnvSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [suiteId, setSuiteId] = useState(presetSuiteId || "");
  const [caseIds, setCaseIds] = useState<string[]>(presetCaseIds);
  const [envId, setEnvId] = useState("");
  const [mode, setMode] = useState<"smoke" | "gate">("smoke");
  const [repeat, setRepeat] = useState(1);
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8000");
  const [maxRequests, setMaxRequests] = useState(30);
  const [outName, setOutName] = useState("");
  const [baseline, setBaseline] = useState("");
  const [job, setJob] = useState<JobPayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (presetSuiteId) {
      setSuiteId(presetSuiteId);
      setCaseIds([]);
    }
  }, [presetSuiteId]);

  useEffect(() => {
    if (presetCaseIds.length) {
      setCaseIds(presetCaseIds);
      if (!presetSuiteId) setSuiteId("");
    }
  }, [presetCaseIds, presetSuiteId]);

  useEffect(() => {
    void (async () => {
      try {
        const [suitePayload, envPayload, runPayload, jobsPayload] = await Promise.all([
          fetchSuites(),
          fetchEnvs(),
          fetchRuns(),
          fetchJobs(),
        ]);
        setSuites(suitePayload.suites || []);
        setEnvs(envPayload.envs || []);
        setRuns(runPayload.runs || []);
        if (!suiteId && !presetCaseIds.length && suitePayload.suites?.[0]) {
          setSuiteId(suitePayload.suites[0].id);
        }
        if (!envId && envPayload.envs?.[0]) setEnvId(envPayload.envs[0].id);
        if (jobsPayload.active_id) {
          const active = (jobsPayload.jobs || []).find(item => item.id === jobsPayload.active_id);
          if (active) setJob(active);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- load once

  useEffect(() => {
    const suite = suites.find(item => item.id === suiteId);
    if (!suite) return;
    setMode(suite.mode === "gate" ? "gate" : "smoke");
    setRepeat(suite.repeat);
    setEnvId(suite.default_env);
  }, [suiteId, suites]);

  useEffect(() => {
    const env = envs.find(item => item.id === envId);
    if (env) setMaxRequests(env.budget);
  }, [envId, envs]);

  const running = useMemo(
    () => job != null && ["queued", "running", "cancelling"].includes(job.status),
    [job],
  );

  useEffect(() => {
    if (!job || !running) return;
    const timer = window.setInterval(() => {
      void fetchJob(job.id)
        .then(setJob)
        .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job, running]);

  async function start() {
    setError("");
    setBusy(true);
    try {
      const created = await createJob({
        case_ids: suiteId ? undefined : caseIds.length ? caseIds : undefined,
        suite_id: suiteId || undefined,
        env_id: envId || undefined,
        mode,
        repeat,
        base_url: baseUrl,
        max_requests: maxRequests,
        out_name: outName.trim() || undefined,
        baseline: baseline || undefined,
      });
      setJob(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function onCancel() {
    if (!job) return;
    setError("");
    try {
      const next = await cancelJob(job.id);
      setJob(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const selectedSuite = suites.find(item => item.id === suiteId) || null;
  const evaluatorId = selectedSuite?.default_evaluator || "—";
  const canStart = Boolean(envId && (suiteId || caseIds.length));
  const showProgress = job != null;

  return (
    <div className="wb-page">
      <div className="wb-page-head">
        <h2>开跑</h2>
        <span className="wb-muted">选一套评测配置，对本地服务跑一遍</span>
      </div>
      {error && (
        <p className="wb-error" role="alert">
          {error}
        </p>
      )}

      <section className="wb-card">
        <div className="wb-form">
          <label>
            套件
            <select
              value={suiteId}
              disabled={running}
              onChange={event => {
                setSuiteId(event.target.value);
                if (event.target.value) setCaseIds([]);
              }}
            >
              {caseIds.length > 0 && !suiteId && (
                <option value="">（按案例 · {caseIds.join(", ")}）</option>
              )}
              {!suites.length && <option value="">（暂无套件）</option>}
              {suites.map(item => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </label>
          <div className="wb-compare-banner">
            环境 <code>{envId || "—"}</code>
            {" · "}
            检查包 <code>{evaluatorId}</code>
            {" · "}
            <strong>{modeLabel(mode)}</strong>
            {selectedSuite?.cases?.length ? (
              <div className="wb-muted" style={{ marginTop: 6 }}>
                将跑 {selectedSuite.cases.length} 个案例：{selectedSuite.cases.join(", ")}
              </div>
            ) : null}
            {!suiteId && caseIds.length > 0 ? (
              <div className="wb-muted" style={{ marginTop: 6 }}>
                按资产页带来的案例开跑：{caseIds.join(", ")}（未绑套件时默认快速检查）
              </div>
            ) : null}
            <div className="wb-muted" style={{ marginTop: 6 }}>
              快速检查：改完提示词或工具后随手验证。正式验收：发版前每案跑 3 次看稳定性。
            </div>
          </div>
        </div>

        <div className="wb-launch-actions">
          <button
            type="button"
            className="wb-chip btn"
            disabled={busy || running || !canStart}
            onClick={() => void start()}
          >
            开始
          </button>
          {running && (
            <button type="button" className="wb-chip bad" onClick={() => void onCancel()}>
              取消
            </button>
          )}
          {job && ["completed", "failed", "cancelled"].includes(job.status) && job.run_id && (
            <button type="button" className="wb-chip ghost" onClick={() => onOpenRun(job.run_id)}>
              打开报告 {job.run_id}
            </button>
          )}
          {!canStart && <span className="wb-muted">请先选择套件</span>}
        </div>

        <details className="wb-advanced">
          <summary>高级（一般不用改）</summary>
          <div className="wb-form" style={{ marginTop: 12 }}>
            <label>
              覆盖环境
              <select
                value={envId}
                disabled={running}
                onChange={event => setEnvId(event.target.value)}
              >
                {envs.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              本地服务地址
              <input
                type="text"
                value={baseUrl}
                disabled={running}
                onChange={event => setBaseUrl(event.target.value)}
              />
            </label>
            <label>
              模型请求上限
              <input
                type="number"
                min={1}
                value={maxRequests}
                disabled={running}
                onChange={event => setMaxRequests(Number(event.target.value) || 30)}
              />
            </label>
            <label>
              输出目录名
              <input
                type="text"
                placeholder="默认按时间生成"
                value={outName}
                disabled={running}
                onChange={event => setOutName(event.target.value)}
              />
            </label>
            <label>
              对比上次运行
              <select
                value={baseline}
                disabled={running}
                onChange={event => setBaseline(event.target.value)}
              >
                <option value="">（无）</option>
                {runs.map(run => (
                  <option key={run.id} value={run.id}>
                    {run.id}
                  </option>
                ))}
              </select>
            </label>
            {baseline ? (
              <p className="wb-muted">
                已选对比运行时，请确认与对方套件、检查包一致，通过率对比才有意义。
              </p>
            ) : null}
          </div>
        </details>

        {showProgress && (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>
              进度 · {job?.status || "—"}
              {job?.gate_passed != null && (
                <span
                  className={`wb-chip ${job.gate_passed ? "ok" : "bad"}`}
                  style={{ marginLeft: 8 }}
                >
                  {job.gate_passed ? "验收通过" : "验收未通过"}
                </span>
              )}
            </h3>
            <p className="wb-muted">
              {[job.suite_id, job.env_id, job.evaluator_id].filter(Boolean).join(" · ") ||
                "未绑定资产 id"}
            </p>
            <p className="wb-muted">
              {job.completed_attempts}/{job.total_attempts}
              {job.current_case
                ? ` · 当前 ${job.current_case}#${job.current_attempt ?? "?"}`
                : ""}
            </p>
            {job.error && <p className="wb-error">{job.error}</p>}
            <pre className="wb-log">{(job.log || []).join("\n") || "（尚无日志）"}</pre>
          </div>
        )}
      </section>
    </div>
  );
}
