import { useCallback, useEffect, useState } from "react";

import {
  type CaseDetail,
  type CaseRunLink,
  type CaseSummary,
  fetchCase,
  fetchCaseRuns,
  fetchCases,
} from "./api";

export function CasesPage({
  caseId,
  onOpenCase,
  onOpenRun,
  onLaunchCase,
}: {
  caseId: string | null;
  onOpenCase: (id: string | null) => void;
  onOpenRun: (runId: string, preferCaseId?: string) => void;
  onLaunchCase: (id: string) => void;
}) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [related, setRelated] = useState<CaseRunLink[]>([]);
  const [error, setError] = useState("");
  const [casesRoot, setCasesRoot] = useState("");

  const loadList = useCallback(async () => {
    setError("");
    try {
      const payload = await fetchCases();
      setCases(payload.cases || []);
      setCasesRoot(payload.cases_root || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!caseId) {
      setDetail(null);
      setRelated([]);
      return;
    }
    let cancelled = false;
    setError("");
    void (async () => {
      try {
        const next = await fetchCase(caseId);
        const runs = await fetchCaseRuns(caseId);
        if (cancelled) return;
        setDetail(next);
        setRelated(runs.runs || []);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  if (error && !cases.length) {
    return <p className="wb-error" role="alert">{error}</p>;
  }

  if (!caseId) {
    return (
      <div className="wb-page">
        <div className="wb-page-head">
          <h2>Case 库</h2>
          <span className="wb-muted">{casesRoot || "evaluation/cases"}</span>
          <button type="button" className="wb-chip ghost" onClick={() => void loadList()}>
            刷新
          </button>
        </div>
        {error && <p className="wb-error" role="alert">{error}</p>}
        {!cases.length ? (
          <p className="wb-muted">还没有案例。把 YAML 放进 evaluation/cases 后刷新。</p>
        ) : (
          <div className="wb-table">
            <div className="wb-table-row head">
              <span>id</span>
              <span>work</span>
              <span>history</span>
              <span>检查项</span>
            </div>
            {cases.map(item => (
              <button
                key={item.id}
                type="button"
                className="wb-table-row link"
                onClick={() => onOpenCase(item.id)}
              >
                <span className="mono">{item.id}</span>
                <span>{item.work}</span>
                <span>{item.history}</span>
                <span>
                  must {item.check_counts.must} · quality {item.check_counts.quality} · human{" "}
                  {item.check_counts.human}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (!detail) {
    return <p className="wb-muted" style={{ padding: "1rem" }}>正在读取案例…</p>;
  }

  return (
    <div className="wb-page">
      <div className="wb-page-head">
        <button type="button" className="wb-chip ghost" onClick={() => onOpenCase(null)}>
          ← Case 列表
        </button>
        <h2 className="mono">{detail.id}</h2>
        <span className="wb-chip">v{detail.version}</span>
        <span className="wb-chip">{detail.history}</span>
        <button type="button" className="wb-chip btn" onClick={() => onLaunchCase(detail.id)}>
          用此 Case 跑测
        </button>
      </div>
      {error && <p className="wb-error" role="alert">{error}</p>}
      <div className="wb-cards">
        <section className="wb-card">
          <h3>作品</h3>
          <p className="mono">{detail.work}</p>
          <p className="wb-path" title={detail.work_dir}>
            {detail.work_dir}
          </p>
        </section>
        <section className="wb-card">
          <h3>作者步骤</h3>
          <ol>
            {detail.steps.map((step, index) => (
              <li key={`${index}-${step.author.slice(0, 12)}`}>
                <div>{step.author}</div>
                {step.focus && (
                  <div className="wb-path" title={step.focus}>
                    {step.focus}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </section>
        <section className="wb-card">
          <h3>预期区域</h3>
          <p className="wb-path" title={detail.expect.region.path}>
            {detail.expect.region.path}
          </p>
          <p className="wb-muted">from: {detail.expect.region.from}</p>
          <p className="wb-muted">to: {detail.expect.region.to}</p>
          {detail.expect.preserve.length > 0 && (
            <ul>
              {detail.expect.preserve.map(frag => (
                <li key={frag} className="mono">
                  {frag}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="wb-card">
          <h3>检查项</h3>
          <ul className="wb-check-list">
            {detail.checks.map(check => (
              <li key={check.id}>
                <span className="wb-chip">{check.level}</span>{" "}
                <span className="mono">{check.id}</span>
                {check.question && <div className="wb-muted">{check.question}</div>}
              </li>
            ))}
          </ul>
        </section>
      </div>
      <section className="wb-card" style={{ marginTop: 12 }}>
        <h3>相关运行</h3>
        {!related.length ? (
          <p className="wb-muted">还没有包含此 Case 的落盘运行。</p>
        ) : (
          <div className="wb-table">
            {related.map(run => (
              <button
                key={run.id}
                type="button"
                className="wb-table-row link"
                onClick={() => onOpenRun(run.id, detail.id)}
              >
                <span className="mono">{run.id}</span>
                <span>{run.mode || "-"}</span>
                <span>{run.gate_passed == null ? "-" : run.gate_passed ? "门禁通过" : "门禁不通过"}</span>
                <span>{run.outcomes.join(", ") || `${run.attempt_count} 次`}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
