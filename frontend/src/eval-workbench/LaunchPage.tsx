import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type CaseSummary,
  type JobPayload,
  type RunSummary,
  cancelJob,
  createJob,
  fetchCases,
  fetchJob,
  fetchJobs,
  fetchRuns,
} from "./api";

export function LaunchPage({
  presetCaseIds,
  onOpenRun,
}: {
  presetCaseIds: string[];
  onOpenRun: (runId: string) => void;
}) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string[]>(presetCaseIds);
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
    setSelected(prev => {
      const merged = new Set([...prev, ...presetCaseIds]);
      return [...merged];
    });
  }, [presetCaseIds]);

  useEffect(() => {
    void (async () => {
      try {
        const [casePayload, runPayload, jobsPayload] = await Promise.all([
          fetchCases(),
          fetchRuns(),
          fetchJobs(),
        ]);
        setCases(casePayload.cases || []);
        setRuns(runPayload.runs || []);
        if (jobsPayload.active_id) {
          const active = (jobsPayload.jobs || []).find(item => item.id === jobsPayload.active_id);
          if (active) setJob(active);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, []);

  useEffect(() => {
    if (mode === "gate" && repeat < 3) setRepeat(3);
    if (mode === "smoke" && repeat !== 1) setRepeat(1);
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps -- only reset defaults on mode change

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

  const toggleCase = useCallback((id: string) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]));
  }, []);

  async function start() {
    setError("");
    setBusy(true);
    try {
      const created = await createJob({
        case_ids: selected,
        mode,
        repeat,
        base_url: baseUrl,
        max_requests: maxRequests,
        out_name: outName.trim() || undefined,
        baseline: mode === "gate" && baseline ? baseline : undefined,
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

  return (
    <div className="wb-page">
      <div className="wb-page-head">
        <h2>跑测</h2>
        <span className="wb-muted">选题后触发 evaluation run；需本机已启动本地模式服务</span>
      </div>
      {error && <p className="wb-error" role="alert">{error}</p>}

      <div className="wb-launch-grid">
        <section className="wb-card">
          <h3>案例</h3>
          <div className="wb-case-pick">
            {cases.map(item => (
              <label key={item.id} className="wb-check-row">
                <input
                  type="checkbox"
                  checked={selected.includes(item.id)}
                  onChange={() => toggleCase(item.id)}
                  disabled={running}
                />
                <span className="mono">{item.id}</span>
                <span className="wb-muted">{item.work}</span>
              </label>
            ))}
            {!cases.length && <p className="wb-muted">没有可跑案例</p>}
          </div>
        </section>

        <section className="wb-card">
          <h3>方式与参数</h3>
          <div className="wb-form">
            <label>
              模式
              <select
                value={mode}
                disabled={running}
                onChange={event => setMode(event.target.value as "smoke" | "gate")}
              >
                <option value="smoke">冒烟</option>
                <option value="gate">完整门禁</option>
              </select>
            </label>
            <label>
              每案次数
              <input
                type="number"
                min={1}
                max={9}
                value={repeat}
                disabled={running}
                onChange={event => setRepeat(Number(event.target.value) || 1)}
              />
            </label>
            <label>
              base-url
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
                placeholder="默认 smoke-时间戳 / gate-时间戳"
                value={outName}
                disabled={running}
                onChange={event => setOutName(event.target.value)}
              />
            </label>
            {mode === "gate" && (
              <label>
                基线运行
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
            )}
          </div>
          <div className="wb-launch-actions">
            <button
              type="button"
              className="wb-chip btn"
              disabled={busy || running || selected.length === 0}
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
                打开结果 {job.run_id}
              </button>
            )}
          </div>
        </section>
      </div>

      {job && (
        <section className="wb-card" style={{ marginTop: 12 }}>
          <h3>
            进度 · {job.status}
            {job.gate_passed != null && (
              <span className={`wb-chip ${job.gate_passed ? "ok" : "bad"}`} style={{ marginLeft: 8 }}>
                {job.gate_passed ? "门禁通过" : "门禁不通过"}
              </span>
            )}
          </h3>
          <p className="wb-muted">
            {job.completed_attempts}/{job.total_attempts}
            {job.current_case
              ? ` · 当前 ${job.current_case}#${job.current_attempt ?? "?"}`
              : ""}
          </p>
          {job.error && <p className="wb-error">{job.error}</p>}
          <pre className="wb-log">{(job.log || []).join("\n") || "（尚无日志）"}</pre>
        </section>
      )}
    </div>
  );
}
