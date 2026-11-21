import { useEffect, useRef, useState } from "react";
import { PAGE_SIZE, post, request, type Check, type Run, type RunDetail, type RunPage, type TaskDetail, runUrl, taskUrl, url, useResource } from "./api";
import { Evidence, TracePanel, versionLabel } from "./Artifacts";
import { ComparisonPanel, ReviewPanel, type ReviewInput } from "./RunActions";
import { Data, displayDate, navigate, Notice, pageOffset, Pagination, Panel, SearchLink, stateLabel, taskKey, Verdict } from "./ui";

const timingNames = { started: "执行时间顺序", not_started: "未启动", unknown: "执行时间未记录" };
type Timing = keyof typeof timingNames;

function ArchiveGroup({ kind, timing, params, items, active, loading, error, offset, hasMore }: {
  kind: string; timing: Timing; params: object; items: Run[]; active?: string; loading: boolean;
  error?: string; offset: number; hasMore: boolean;
}) {
  return <Panel title={timingNames[timing]}>
    <Notice loading={loading} error={error} />
    {items.length > 0 ? <ol className="wb-records">{items.map(run => <li key={run.run_id}>
      <SearchLink to={{ ...params, run_id: run.run_id }} aria-current={active === run.run_id ? "true" : undefined}>
        <div className="wb-run-line"><strong>{run.title ?? run.run_id}</strong><Verdict value={run.verdict} /></div>
        <small>{run.task ? `${run.task.library_id} / ${run.task.task_id} · r${run.task.revision}` : "独立回归"}</small>
        <small>{timing === "started" ? displayDate(run.started_at) : timingNames[timing]}</small>
      </SearchLink>
    </li>)}</ol> : (!loading && !error && <p className="muted">{kind ? "该类别没有匹配的记录。" : "没有归档记录。"}</p>)}
    <Pagination offset={offset} more={hasMore} change={n => navigate({ ...params, [timing]: n || undefined })} />
  </Panel>;
}

export function Runs({ params }: { params: URLSearchParams }) {
  const selection = taskKey(params);
  const kind = params.get("kind") === "task" || params.get("kind") === "regression" ? params.get("kind")! : "";
  const filters = { ...selection, kind };
  const startedOffset = pageOffset(params.get("started"));
  const notStartedOffset = pageOffset(params.get("not_started"));
  const unknownOffset = pageOffset(params.get("unknown"));
  const query = (timing: Timing, offset: number) => url("/api/runs", { ...filters, timing, offset, limit: PAGE_SIZE });
  const started = useResource<RunPage>(query("started", startedOffset));
  const notStarted = useResource<RunPage>(query("not_started", notStartedOffset));
  const unknown = useResource<RunPage>(query("unknown", unknownOffset));
  const chosen = params.get("run_id");
  const pages = [["started", started, startedOffset], ["not_started", notStarted, notStartedOffset], ["unknown", unknown, unknownOffset]] as const;
  const active = chosen ?? started.data?.default_run_id ?? started.data?.items[0]?.run_id
    ?? (!started.loading ? notStarted.data?.items[0]?.run_id ?? unknown.data?.items[0]?.run_id : undefined);
  const listParams = { page: "runs", ...filters, started: startedOffset || undefined, not_started: notStartedOffset || undefined, unknown: unknownOffset || undefined };
  const baselineOptions = started.data?.items ?? [];
  function refreshLists() { started.refresh(); notStarted.refresh(); unknown.refresh(); }
  return <>
    <header className="wb-heading"><p className="eyebrow">03 / RUN ARCHIVE</p><h1>运行与复盘</h1><p className="muted">按实际执行时间查看评测档案；选中的基线不会改变排序。</p></header>
    <div className="wb-split" data-selected={Boolean(chosen)}>
      <div className="wb-list wb-stack">
        <Panel title="运行档案">
          <label>运行类别<select value={kind} onChange={e => navigate({ page: "runs", ...selection, kind: e.target.value })}>
            <option value="">全部类别</option><option value="task">任务运行</option><option value="regression">独立回归</option>
          </select></label>
          {selection && <div><p>{selection.library_id} / {selection.task_id} · r{selection.revision}</p><SearchLink to={{ page: "runs", kind }}>清除任务筛选</SearchLink></div>}
        </Panel>
        {pages.map(([timing, page, offset]) => <ArchiveGroup key={timing} kind={kind} timing={timing} params={listParams} loading={page.loading} error={page.error}
          items={page.data?.items ?? []} active={active} offset={offset} hasMore={page.data?.has_more ?? false} />)}
      </div>
      <div className="wb-selection wb-stack">
        {chosen && <SearchLink className="narrow-back" to={listParams}>返回运行列表</SearchLink>}
        {active ? <RunInspector key={active} id={active} baselineOptions={baselineOptions} refreshLists={refreshLists} /> :
          <Panel title="运行详情"><p className="muted">{pages.some(([, page]) => page.loading) ? "正在读取运行档案…" : "没有可查看的运行。"}</p></Panel>}
      </div>
    </div>
  </>;
}

const tabs = { trace: "Trace 时间线", evidence: "证据", review: "人工复核", compare: "问题与回归" };
type Tab = keyof typeof tabs;

function RunInspector({ id, baselineOptions, refreshLists }: { id: string; baselineOptions: Run[]; refreshLists: () => void }) {
  const [tab, setTab] = useState<Tab>("trace");
  const [lane, setLane] = useState<"flow" | "agent">("flow");
  const [target, setTarget] = useState({ id: "", write: false, revision: 0 });
  const [reference, setReference] = useState("");
  const [evidenceVersion, setEvidenceVersion] = useState("");
  const [unboundReference, setUnboundReference] = useState(false);
  const [reportVersion, setReportVersion] = useState("");
  const [action, setAction] = useState<{ busy?: boolean; message?: string; error?: string }>({});
  const [reviewGeneration, setReviewGeneration] = useState(0);
  const pending = useRef(false);
  const latest = useResource<RunDetail>(runUrl(id), { keepDataOnError: true });
  const historical = useResource<RunDetail>(reportVersion ? url(runUrl(id), { report_version: reportVersion }) : null);
  const detail = latest.data;
  const task = useResource<TaskDetail>(detail?.run.task ? taskUrl(detail.run.task) : null);
  const taskObject = task.data?.task;
  const running = detail?.run.execution_status === "running";
  const refresh = latest.refresh;
  const failures = latest.failures ?? 0;
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(refresh, Math.min(900 * 2 ** failures, 15000));
    return () => window.clearTimeout(timer);
  }, [running, detail, failures, refresh]);
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (running) setWasRunning(true);
    else if (wasRunning && detail) {
      setWasRunning(false);
      refreshLists();
    }
  }, [running, wasRunning, detail, refreshLists]);
  function jumpTrace(span: string, write = false) {
    setTarget(previous => ({ id: span, write, revision: previous.revision + 1 }));
    setLane("agent");
    setTab("trace");
  }
  function jumpEvidence(ref: string) {
    const bound = !reportVersion ? ""
      : reportVersion === "legacy-current" ? "legacy-current"
      : historical.data?.report_version === reportVersion ? historical.data.evidence_version : undefined;
    setEvidenceVersion(bound ?? "");
    setUnboundReference(bound === undefined);
    setReference(ref);
    setTab("evidence");
  }
  async function updateRun(operation: "review" | "regrade", body: object) {
    if (pending.current || !detail) return;
    pending.current = true;
    setAction({ busy: true });
    let posted = false;
    try {
      await post<RunDetail>(runUrl(id, `/${operation}`), body);
      posted = true;
      const verified = await request<RunDetail>(runUrl(id));
      if (verified.run.run_id !== id) throw new Error("读回的运行 ID 不匹配。");
      latest.accept(verified);
      refreshLists();
      if (operation === "review") {
        setReviewGeneration(n => n + 1);
        setReportVersion("");
        setAction({ message: "已读取提交后的报告与复核状态。" });
      } else {
        const added = verified.report_versions.filter(version => !detail.report_versions.includes(version)).at(-1);
        setReportVersion(added && added !== verified.report_version ? added : "");
        setAction({ message: "已读取重判后的运行；没有启动新运行。" });
      }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setAction({ error: posted ? `写入已返回，但无法读回核对：${message}` : message });
      if (!posted && operation === "review") refresh();
    } finally { pending.current = false; }
  }
  if (!detail) return <Panel title="运行详情"><Notice {...latest} />{latest.error && <button onClick={refresh}>重试读取运行</button>}</Panel>;
  const run = detail.run;
  const reportDetail = reportVersion ? historical.data : detail;
  const report = reportDetail?.report;
  const groups: Record<string, Check[]> = { 确定性检查: [], 语义检查: [], 未分类检查: [] };
  for (const check of report?.checks ?? []) {
    const group = taskObject && Object.prototype.hasOwnProperty.call(taskObject.code_graders, check.check_id) ? "确定性检查"
      : (taskObject && Object.prototype.hasOwnProperty.call(taskObject.llm_graders, check.check_id)) || Object.prototype.hasOwnProperty.call(reportDetail?.review_inputs ?? {}, check.check_id) ? "语义检查" : "未分类检查";
    groups[group].push(check);
  }
  return <>
    <Panel title={run.title ?? run.run_id}>
      <p className="eyebrow">{run.task ? `${run.task.library_id} / ${run.task.task_id} · r${run.task.revision}` : "独立回归 · 不计入任务判定"}</p>
      <p className="muted">{run.run_id} · {run.model ?? "模型未记录"} · {run.started_at ? displayDate(run.started_at) : run.execution_status === "not_started" ? "未启动" : "执行时间未记录"}</p>
      <div className="wb-states">
        <span>执行状态 <span className="badge">{stateLabel(run.execution_status)}</span></span>
        <span>业务判定 <Verdict value={run.verdict ?? detail.report?.verdict} /></span>
        <span>复核状态 <span className="badge">{stateLabel(detail.review_status)}</span></span>
      </div>
      {run.task && <div className="wb-actions">
        <SearchLink to={{ page: "tasks", ...run.task }}>打开任务定义</SearchLink>
        <SearchLink to={{ page: "launch", ...run.task, baseline_id: run.run_id }}>按原任务配置复跑</SearchLink>
      </div>}
      {latest.error && <Notice error={latest.error} />}
      <Notice error={action.error} />
      {action.message && <p role="status">{action.message}</p>}
    </Panel>
    <div className="wb-inspector-grid">
      <Panel title="逐项判定">
        {detail.report_versions.length > 0 && <label>报告版本<select value={reportVersion} onChange={e => setReportVersion(e.target.value)}>
          <option value="">当前报告</option>{detail.report_versions.map(v => <option key={v} value={v}>{versionLabel(v)}</option>)}
        </select></label>}
        {reportVersion && <p className="muted">历史报告只读；顶部状态仍对应当前报告。</p>}
        <Notice {...historical} />
        {report ? <>
          <p>报告判定 <Verdict value={report.verdict} /></p>
          {Object.entries(groups).filter(([, checks]) => checks.length).map(([group, checks]) => <section className="wb-stack" key={group}>
            <h3 className="wb-group">{group}</h3>
            {checks.map(check => {
              const grader = taskObject?.code_graders[check.check_id] ?? taskObject?.llm_graders[check.check_id];
              const role = check.role ?? grader?.role;
              const writeCheck = grader?.rule.type === "workspace_diff" || check.evidence_refs.some(ref => ref.startsWith("workspace_after"));
              return <article key={`${check.case_id}/${check.step_id}/${check.check_id}`}>
                <div className="wb-run-line"><h3>{check.check_id}</h3><Verdict value={check.verdict} /></div>
                <p className="muted">{[check.case_id, check.step_id, role === "required" ? "必过" : role === "diagnostic" ? "诊断" : role].filter(Boolean).join(" · ")}</p>
                <p>{check.reason || "理由未记录"}</p>
                {check.judge && <details><summary>Judge 对照（不作裁决）</summary><Data value={check.judge} /></details>}
                <div className="wb-actions wb-refs">
                  {check.evidence_refs.map(ref => <button key={ref} onClick={() => /^trace[/:]/.test(ref) ? jumpTrace(ref.slice(6)) : jumpEvidence(ref)}>{ref}</button>)}
                  {check.trace_refs?.map(ref => <button key={ref} onClick={() => jumpTrace(ref)}>Trace · {ref}</button>)}
                  {writeCheck && <button onClick={() => jumpTrace("", true)}>查看写入相关 Trace</button>}
                  {group === "语义检查" && !reportVersion && <button onClick={() => setTab("review")}>查看人工复核</button>}
                </div>
              </article>;
            })}
          </section>)}
          {!report.checks.length && <p className="muted">未记录检查条目。</p>}
        </> : (!historical.loading && !historical.error && <p className="muted">此版本没有逐项报告。</p>)}
      </Panel>
      <section className="wb-panel" aria-label="运行细节">
        <div className="wb-tabs" role="tablist" aria-label="运行细节">
          {(Object.entries(tabs) as [Tab, string][]).map(([value, label], index, entries) => <button key={value} id={`tab-${value}`} role="tab" type="button" aria-selected={value === tab} aria-controls="run-tab-panel" tabIndex={value === tab ? 0 : -1}
            onClick={() => setTab(value)} onKeyDown={event => {
              const next = event.key === "ArrowLeft" ? (index + entries.length - 1) % entries.length : event.key === "ArrowRight" ? (index + 1) % entries.length
                : event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1 : undefined;
              if (next == null) return;
              event.preventDefault();
              const nextTab = entries[next][0];
              setTab(nextTab);
              document.getElementById(`tab-${nextTab}`)?.focus();
            }}>{label}</button>)}
        </div>
        <div className="wb-body" id="run-tab-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
          {tab === "trace" && <TracePanel detail={detail} lane={lane} changeLane={setLane} target={target} jump={jumpTrace} />}
          {tab === "evidence" && <Evidence detail={detail} version={evidenceVersion} change={setEvidenceVersion} reference={reference} unbound={unboundReference} />}
          {tab === "review" && <ReviewPanel key={`${detail.evidence_sha256}:${reviewGeneration}`} detail={detail} historical={Boolean(reportVersion)} busy={Boolean(action.busy)}
            save={(evidence_sha256: string, checks: Record<string, ReviewInput>) => updateRun("review", { evidence_sha256, checks })} />}
          {tab === "compare" && <ComparisonPanel detail={detail} options={baselineOptions} busy={Boolean(action.busy)}
            regrade={evidence_version => updateRun("regrade", { evidence_version })} />}
        </div>
      </section>
    </div>
  </>;
}
