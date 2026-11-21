import { PAGE_SIZE, type Grader, type Rule, type TaskDetail, type TaskPage, taskUrl, url, useResource } from "./api";
import { Data, navigate, Notice, pageOffset, Pagination, Panel, SearchLink, taskKey } from "./ui";

export function GraderRule({ rule }: { rule: Rule }) {
  return typeof rule.criterion === "string" ? <>
    <p>{rule.criterion}</p>
    {Object.entries(rule).filter(([key]) => key !== "criterion").map(([key, value]) => <details key={key}>
      <summary>{({ pass_examples: "通过示例", fail_examples: "不通过示例" } as Record<string, string>)[key] ?? key}</summary><Data value={value} />
    </details>)}
  </> : <Data value={rule} />;
}

function Graders({ title, items }: { title: string; items: Record<string, Grader> }) {
  return <Panel title={title}>{Object.keys(items).length ? Object.entries(items).map(([id, grader]) => <article key={id}>
    <h3>{id} <span className="badge">{grader.role === "required" ? "必过" : "诊断"}</span></h3>
    <p className="muted">检查点 · {grader.checkpoint.case} / {grader.checkpoint.step}</p>
    <p>所需证据 · {grader.evidence.join("、") || "无"}</p>
    <GraderRule rule={grader.rule} />
  </article>) : <p className="muted">没有此类检查。</p>}</Panel>;
}

export function Tasks({ params }: { params: URLSearchParams }) {
  const query = params.get("query") ?? "";
  const library = params.get("library") ?? "";
  const offset = pageOffset(params.get("offset"));
  const selection = taskKey(params);
  const dashboard = { page: "tasks", query, library, offset: offset || undefined };
  const list = useResource<TaskPage>(url("/api/tasks", { library_id: library, query, offset, limit: PAGE_SIZE }));
  const catalog = useResource<TaskPage>(url("/api/tasks", { limit: 1 }));
  const task = useResource<TaskDetail>(selection ? taskUrl(selection) : null);
  const libraries = new Set([...(catalog.data?.libraries ?? []), ...(list.data?.libraries ?? []), ...(library ? [library] : [])]);
  return <>
    <header className="wb-heading"><p className="eyebrow">01 / TASK LIBRARY</p><h1>任务库</h1></header>
    <div className="wb-split" data-selected={Boolean(selection)}>
      <Panel title="任务列表" className="wb-list">
        <div className="wb-fields">
          <label>任务库筛选<select value={library} onChange={event => navigate({ ...dashboard, library: event.target.value, offset: undefined, ...selection }, true)}>
            <option value="">全部任务库</option>{[...libraries].map(id => <option key={id}>{id}</option>)}
          </select></label>
          <label>搜索任务<input type="search" placeholder="任务 ID 或标题" value={query} onChange={event => navigate({ ...dashboard, query: event.target.value, offset: undefined, ...selection }, true)} /></label>
        </div>
        <Notice {...list} />
        {list.data && (!list.data.items.length ? <p className="muted">没有匹配的任务。</p> : <ul className="wb-records">
          {list.data.items.map(row => {
            const { library_id, task_id, revision } = row;
            return <li key={`${library_id}/${task_id}`}><SearchLink to={{ ...dashboard, library_id, task_id, revision }} aria-current={selection?.task_id === task_id && selection.library_id === library_id ? "true" : undefined}>
              <strong>{row.title}</strong><small>{library_id} / {task_id} · r{revision}</small>
            </SearchLink></li>;
          })}
        </ul>)}
        <Pagination offset={offset} more={list.data?.has_more ?? false} change={n => navigate({ ...dashboard, offset: n || undefined, ...selection })} />
      </Panel>
      <div className="wb-selection wb-stack">
        {selection && <SearchLink className="narrow-back" to={dashboard}>返回任务列表</SearchLink>}
        <Notice {...task} />
        {!selection && <Panel title="任务详情"><p className="muted">从列表选择任务，查看场景、作者输入和检查判据。</p></Panel>}
        {selection && task.data && <>
          <Panel title={task.data.task.scenario.title}>
            <p className="eyebrow">{selection.library_id} / {selection.task_id} · Schema v{task.data.task.schema_version}</p>
            <p>{task.data.task.scenario.description}</p>
            <div className="wb-actions">
              <label>任务修订<select value={selection.revision} onChange={event => navigate({ ...dashboard, ...selection, revision: Number(event.target.value) })}>
                {task.data.revisions.map(revision => <option key={revision} value={revision}>r{revision}</option>)}
              </select></label>
              <SearchLink to={{ page: "launch", ...selection }}>核对运行配置</SearchLink>
              <SearchLink to={{ page: "runs", ...selection }}>查看该修订的运行</SearchLink>
            </div>
            {!task.data.runnable && <p className="muted">不可运行 · {task.data.reason ?? "未配置 Runner"}</p>}
          </Panel>
          <Panel title="场景实例与作者输入">
            {Object.entries(task.data.task.scenario.cases).map(([id, instance]) => <article key={id}>
              <h3>{instance.title} <small>{id}</small></h3>
              <details><summary>隔离初态与焦点</summary><Data value={instance.fixture} /></details>
              <ol>{instance.steps.map(step => <li key={step.id}><strong>{step.id}</strong><Data value={task.data?.task.scenario.messages[step.message]} /></li>)}</ol>
            </article>)}
          </Panel>
          <Graders title="确定性检查" items={task.data.task.code_graders} />
          <Graders title="语义检查" items={task.data.task.llm_graders} />
        </>}
      </div>
    </div>
  </>;
}
