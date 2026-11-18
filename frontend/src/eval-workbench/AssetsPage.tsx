import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type CaseDetail,
  type CaseSummary,
  type EnvSummary,
  type EvaluatorSummary,
  type SuiteSummary,
  fetchCase,
  fetchCases,
  fetchEnvs,
  fetchEvaluators,
  fetchSuites,
} from "./api";
import { modeShort } from "./labels";
import type { AssetTab } from "./routing";

export type { AssetTab };

const TAB_LABEL: Record<AssetTab, string> = {
  suites: "Task 集",
  envs: "评测环境",
  evaluators: "检查项",
};

const TAB_HINT: Record<AssetTab, string> = {
  suites: "点案例展开步骤、预期与检查项；套件可折叠查看绑定",
  envs: "运行条件档案 · 点开查看预算与采证",
  evaluators: "检查包 · 必须项进验收，诊断项只用于定位",
}

function roleLabel(role: string) {
  return role === "diagnostic" ? "诊断" : "必需";
}

function levelTone(level: string): "ok" | "warn" | "muted" {
  if (level === "must") return "ok";
  if (level === "quality") return "warn";
  return "muted";
}

function TaskDetailBody({
  detail,
  loading,
  error,
  onLaunch,
}: {
  detail: CaseDetail | null;
  loading: boolean;
  error: string;
  onLaunch?: (caseId: string) => void;
}) {
  if (loading) return <p className="wb-muted">正在读取 Task 详情…</p>;
  if (error) return <p className="wb-error">{error}</p>;
  if (!detail) return <p className="wb-muted">无详情</p>;

  return (
    <div className="wb-task-detail">
      <div className="wb-asset-grid">
        <section>
          <h4>作品</h4>
          <p>
            <code>{detail.work}</code>
          </p>
          <p className="wb-path" title={detail.work_dir}>
            {detail.work_dir}
          </p>
          <p className="wb-muted">history · {detail.history}</p>
        </section>
        <section>
          <h4>预期区域</h4>
          <p className="wb-path" title={detail.expect.region.path}>
            {detail.expect.region.path}
          </p>
          <p className="wb-muted">from · {detail.expect.region.from}</p>
          <p className="wb-muted">to · {detail.expect.region.to}</p>
          {detail.expect.preserve.length > 0 && (
            <ul className="wb-pill-list" style={{ marginTop: 8 }}>
              {detail.expect.preserve.map(frag => (
                <li key={frag}>
                  <code>{frag}</code>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section style={{ marginTop: "0.85rem" }}>
        <h4>作者步骤</h4>
        <ol className="wb-step-list">
          {detail.steps.map((step, index) => (
            <li key={`${index}-${step.author.slice(0, 16)}`}>
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

      <section style={{ marginTop: "0.85rem" }}>
        <h4>检查项</h4>
        <ul className="wb-check-grid">
          {detail.checks.map(check => (
            <li key={check.id}>
              <div className="wb-check-grid-top">
                <code>{check.id}</code>
                <span className={`wb-tag ${levelTone(check.level)}`}>{check.level}</span>
                {check.role && (
                  <span className={`wb-tag ${check.role === "diagnostic" ? "muted" : "ok"}`}>
                    {roleLabel(check.role)}
                  </span>
                )}
              </div>
              {check.question && <p className="wb-check-q">{check.question}</p>}
            </li>
          ))}
        </ul>
      </section>

      <p className="wb-path" style={{ marginTop: "0.75rem" }} title={detail.source}>
        {detail.source}
      </p>

      {onLaunch && (
        <div className="wb-asset-actions">
          <button
            type="button"
            className="wb-chip btn"
            onClick={event => {
              event.stopPropagation();
              onLaunch(detail.id);
            }}
          >
            带到开跑
          </button>
        </div>
      )}
    </div>
  );
}

export function AssetsPage({
  tab,
  onTab: _onTab,
  onLaunchSuite,
  onLaunchCase,
}: {
  tab: AssetTab;
  onTab: (tab: AssetTab) => void;
  onLaunchSuite: (suiteId: string) => void;
  onLaunchCase: (caseId: string) => void;
}) {
  void _onTab;
  const [suites, setSuites] = useState<SuiteSummary[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [envs, setEnvs] = useState<EnvSummary[]>([]);
  const [evaluators, setEvaluators] = useState<EvaluatorSummary[]>([]);
  const [error, setError] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [taskDetails, setTaskDetails] = useState<Record<string, CaseDetail>>({});
  const [taskLoading, setTaskLoading] = useState<Record<string, boolean>>({});
  const [taskErrors, setTaskErrors] = useState<Record<string, string>>({});

  const suiteByCase = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const suite of suites) {
      for (const caseId of suite.cases) {
        const list = map.get(caseId) || [];
        list.push(suite.id);
        map.set(caseId, list);
      }
    }
    return map;
  }, [suites]);

  const reload = useCallback(async () => {
    setError("");
    try {
      const [s, c, e, ev] = await Promise.all([
        fetchSuites(),
        fetchCases(),
        fetchEnvs(),
        fetchEvaluators(),
      ]);
      setSuites(s.suites || []);
      setCases(c.cases || []);
      setEnvs(e.envs || []);
      setEvaluators(ev.evaluators || []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setOpenKey(null);
  }, [tab]);

  async function ensureTaskDetail(caseId: string) {
    if (taskDetails[caseId] || taskLoading[caseId]) return;
    setTaskLoading(prev => ({ ...prev, [caseId]: true }));
    setTaskErrors(prev => ({ ...prev, [caseId]: "" }));
    try {
      const detail = await fetchCase(caseId);
      setTaskDetails(prev => ({ ...prev, [caseId]: detail }));
    } catch (reason) {
      setTaskErrors(prev => ({
        ...prev,
        [caseId]: reason instanceof Error ? reason.message : String(reason),
      }));
    } finally {
      setTaskLoading(prev => ({ ...prev, [caseId]: false }));
    }
  }

  function toggle(key: string) {
    setOpenKey(prev => {
      const next = prev === key ? null : key;
      if (next?.startsWith("task:")) {
        void ensureTaskDetail(next.slice("task:".length));
      }
      return next;
    });
  }

  const [selectedEnvId, setSelectedEnvId] = useState<string | null>(null);
  const selectedEnv = useMemo(
    () => envs.find(item => item.id === selectedEnvId) || envs[0] || null,
    [envs, selectedEnvId],
  );

  useEffect(() => {
    if (tab === "envs" && !selectedEnvId && envs[0]) setSelectedEnvId(envs[0].id);
  }, [tab, envs, selectedEnvId]);

  return (
    <div className="wb-page wb-assets">
      <div className="wb-page-head wb-assets-head">
        <div className="wb-assets-title">
          <h2>{TAB_LABEL[tab]}</h2>
          <p className="wb-muted">{TAB_HINT[tab]}</p>
        </div>
        <button type="button" className="wb-chip ghost" onClick={() => void reload()}>
          刷新
        </button>
      </div>

      {error && (
        <p className="wb-error" role="alert">
          {error}
        </p>
      )}

      {tab === "suites" && (
        <div className="wb-asset-stack">
          <section className="wb-asset-section">
            <div className="wb-asset-section-head">
              <h3>Suite</h3>
              <span className="wb-muted">{suites.length} 个 · 只读 YAML</span>
            </div>
            {!suites.length && <p className="wb-empty">尚无 Suite。</p>}
            {suites.length > 0 && (
              <div className="wb-table-wrap">
                <table className="wb-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>跑法</th>
                      <th>案例数</th>
                      <th>默认环境 / 检查包</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {suites.map(item => {
                      const key = `suite:${item.id}`;
                      const open = openKey === key;
                      return (
                        <tr key={item.id} className={open ? "open" : ""}>
                          <td>
                            <button type="button" className="wb-linkish" onClick={() => toggle(key)}>
                              <code>{item.id}</code>
                            </button>
                            {item.desc && <div className="wb-muted">{item.desc}</div>}
                            {open && (
                              <div className="wb-table-inline-detail">
                                <ul className="wb-pill-list">
                                  {item.cases.map(caseId => (
                                    <li key={caseId}>
                                      <button
                                        type="button"
                                        className="wb-pill-link"
                                        onClick={() => {
                                          void ensureTaskDetail(caseId);
                                          toggle(`task:${caseId}`);
                                        }}
                                      >
                                        <code>{caseId}</code>
                                      </button>
                                    </li>
                                  ))}
                                </ul>
                                <p className="wb-path" title={item.source}>
                                  {item.source}
                                </p>
                              </div>
                            )}
                          </td>
                          <td>
                            <span className="wb-tag">{modeShort(item.mode)}</span>
                            <span className="wb-muted"> · 每案 {item.repeat} 次</span>
                          </td>
                          <td>{item.cases.length}</td>
                          <td className="wb-muted">
                            {item.default_env} · {item.default_evaluator}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="wb-chip btn"
                              onClick={() => onLaunchSuite(item.id)}
                            >
                              带到开跑
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="wb-asset-section">
            <div className="wb-asset-section-head">
              <h3>案例摘要</h3>
              <span className="wb-muted">{cases.length} 条 · 展开看步骤与检查项</span>
            </div>
            <div className="wb-asset-list">
              {!cases.length && (
                <p className="wb-empty">尚无 Task。把 YAML 放进 evaluation/cases 后刷新。</p>
              )}
              {cases.map(item => {
                const key = `task:${item.id}`;
                const open = openKey === key;
                const suitesFor = suiteByCase.get(item.id) || [];
                return (
                  <article
                    key={item.id}
                    id={`wb-task-${item.id}`}
                    className={`wb-asset-card ${open ? "open" : ""}`}
                  >
                    <button
                      type="button"
                      className="wb-asset-card-head"
                      aria-expanded={open}
                      onClick={() => toggle(key)}
                    >
                      <span className="wb-asset-chevron" aria-hidden />
                      <div className="wb-asset-main">
                        <div className="wb-asset-id-row">
                          <code className="wb-asset-id">{item.id}</code>
                          {suitesFor.map(suiteId => (
                            <span key={suiteId} className="wb-tag">
                              {suiteId}
                            </span>
                          ))}
                        </div>
                        <p className="wb-asset-desc" title={item.work}>
                          {item.work}
                        </p>
                      </div>
                      <div className="wb-asset-meta">
                        <span>must {item.check_counts.must}</span>
                        <span>quality {item.check_counts.quality}</span>
                        <span>human {item.check_counts.human}</span>
                        {onLaunchCase && (
                          <button
                            type="button"
                            className="wb-chip ghost"
                            onClick={event => {
                              event.stopPropagation();
                              onLaunchCase(item.id);
                            }}
                          >
                            带到开跑
                          </button>
                        )}
                      </div>
                    </button>
                    {open && (
                      <div className="wb-asset-body">
                        <TaskDetailBody
                          detail={taskDetails[item.id] || null}
                          loading={Boolean(taskLoading[item.id])}
                          error={taskErrors[item.id] || ""}
                          onLaunch={onLaunchCase}
                        />
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        </div>
      )}

      {tab === "envs" && (
        <div className="wb-split-pane">
          <aside className="wb-split-list">
            {!envs.length && <p className="wb-empty">尚无 Env。</p>}
            {envs.map(item => (
              <button
                key={item.id}
                type="button"
                className={`wb-split-item ${selectedEnv?.id === item.id ? "on" : ""}`}
                onClick={() => setSelectedEnvId(item.id)}
              >
                <code>{item.id}</code>
                <span className="wb-muted">预算 {item.budget}</span>
              </button>
            ))}
          </aside>
          <div className="wb-split-detail">
            {!selectedEnv && <p className="wb-empty">选择左侧 Env 查看档案。</p>}
            {selectedEnv && (
              <>
                <h3>
                  <code>{selectedEnv.id}</code>
                  <span className="wb-tag soft">v{selectedEnv.version}</span>
                </h3>
                <p className="wb-muted">{selectedEnv.desc || "无描述"}</p>
                <dl className="wb-kv wb-kv-wide">
                  <div>
                    <dt>模型请求上限</dt>
                    <dd>{selectedEnv.budget}</dd>
                  </div>
                  <div>
                    <dt>单轮超时</dt>
                    <dd>{selectedEnv.timeout_s} 秒</dd>
                  </div>
                  <div>
                    <dt>输入采证</dt>
                    <dd>{selectedEnv.capture_inputs ? "开启 capture_inputs" : "关闭"}</dd>
                  </div>
                  <div>
                    <dt>作品基线</dt>
                    <dd>
                      <code>{selectedEnv.work_baseline}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>源文件</dt>
                    <dd className="wb-path" title={selectedEnv.source}>
                      {selectedEnv.source}
                    </dd>
                  </div>
                  <div>
                    <dt>base_url</dt>
                    <dd className="wb-muted">由开跑页 / CLI 启动时覆盖，不做在线 Agent 向导</dd>
                  </div>
                </dl>
              </>
            )}
          </div>
        </div>
      )}

      {tab === "evaluators" && (
        <div className="wb-asset-list">
          {!evaluators.length && <p className="wb-empty">尚无检查包。</p>}
          {evaluators.map(pack => {
            const key = `eval:${pack.id}`;
            const open = openKey === key;
            const requiredChecks = pack.checks.filter(c => c.role !== "diagnostic");
            const diagnosticChecks = pack.checks.filter(c => c.role === "diagnostic");
            return (
              <article key={pack.id} className={`wb-asset-card ${open ? "open" : ""}`}>
                <button
                  type="button"
                  className="wb-asset-card-head"
                  aria-expanded={open}
                  onClick={() => toggle(key)}
                >
                  <span className="wb-asset-chevron" aria-hidden />
                  <div className="wb-asset-main">
                    <div className="wb-asset-id-row">
                      <code className="wb-asset-id">{pack.id}</code>
                      <span className="wb-tag soft">v{pack.version}</span>
                      <span className="wb-tag muted">脚本 / 规则</span>
                    </div>
                    <p className="wb-asset-desc">{pack.desc || "无描述"}</p>
                  </div>
                  <div className="wb-asset-meta">
                    <span>必需 {requiredChecks.length}</span>
                    <span>诊断 {diagnosticChecks.length}</span>
                  </div>
                </button>
                {open && (
                  <div className="wb-asset-body">
                    <p className="wb-path" title={pack.source}>
                      {pack.source}
                    </p>
                    <div className="wb-eval-columns">
                      <section>
                        <h4 className="wb-ok-title">必需项</h4>
                        <ul className="wb-check-grid">
                          {requiredChecks.map(check => (
                            <li key={check.id}>
                              <div className="wb-check-grid-top">
                                <code>{check.id}</code>
                                <span className={`wb-tag ${levelTone(check.level)}`}>{check.level}</span>
                                <span className="wb-tag ok">{roleLabel(check.role)}</span>
                              </div>
                              {check.impl && (
                                <div className="wb-muted">
                                  impl · <code>{check.impl}</code>
                                </div>
                              )}
                              {check.question && <p className="wb-check-q">{check.question}</p>}
                            </li>
                          ))}
                          {!requiredChecks.length && <li className="wb-muted">无</li>}
                        </ul>
                      </section>
                      <section>
                        <h4 className="wb-warn-title">诊断项</h4>
                        <ul className="wb-check-grid">
                          {diagnosticChecks.map(check => (
                            <li key={check.id}>
                              <div className="wb-check-grid-top">
                                <code>{check.id}</code>
                                <span className={`wb-tag ${levelTone(check.level)}`}>{check.level}</span>
                                <span className="wb-tag muted">{roleLabel(check.role)}</span>
                              </div>
                              {check.impl && (
                                <div className="wb-muted">
                                  impl · <code>{check.impl}</code>
                                </div>
                              )}
                              {check.question && <p className="wb-check-q">{check.question}</p>}
                            </li>
                          ))}
                          {!diagnosticChecks.length && <li className="wb-muted">无</li>}
                        </ul>
                      </section>
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
