import { useEffect, useRef, useState } from "react";
import { post, type TaskDetail, type TaskKey, taskUrl, useResource } from "./api";
import { Data, navigate, Notice, Panel, SearchLink, taskKey } from "./ui";

type Preflight = { model?: string; connection_id?: string; evaluator_version?: string } & (
  { valid: true; approval_token: string } | { valid: false; reason?: string }
);

export function Launch({ params }: { params: URLSearchParams }) {
  const key = taskKey(params);
  const baseline = params.get("baseline_id") ?? undefined;
  return <>
    <header className="wb-heading"><p className="eyebrow">02 / RUN SETUP</p><h1>核对运行配置</h1>
      <p className="muted">先核对任务，再执行预检。只有明确开始运行才会调用在线模型。</p>
    </header>
    {key ? <Configuration key={`${taskUrl(key)}:${baseline ?? ""}`} selection={key} baseline={baseline} /> :
      <Panel title="尚未选择任务"><p>从任务库选择任务与修订，再核对配置。</p><SearchLink to={{ page: "tasks" }}>打开任务库</SearchLink></Panel>}
  </>;
}

function Configuration({ selection, baseline }: { selection: TaskKey; baseline?: string }) {
  const task = useResource<TaskDetail>(taskUrl(selection));
  const [preview, setPreview] = useState<Preflight>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<"preview" | "start">();
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const runnable = Boolean(task.data?.runnable);
  const payload = { ...selection, ...(baseline ? { baseline_id: baseline } : {}) };
  const scenario = task.data?.task.scenario;
  const approvalToken = preview?.valid ? preview.approval_token : undefined;

  async function perform(action: "preview" | "start") {
    if (pending.current || !runnable || (action === "start" && !approvalToken)) return;
    pending.current = true;
    setBusy(action);
    setPreview(undefined);
    setError(undefined);
    try {
      if (action === "preview") {
        const result = await post<Preflight>("/api/preflight", payload);
        if (result.valid && (typeof result.approval_token !== "string" || !result.approval_token)) {
          throw new Error("预检未返回启动凭证，请重新执行预检。");
        }
        setPreview(result);
      } else {
        const result = await post<{ run_id: string }>("/api/runs", { ...payload, approval_token: approvalToken });
        if (!result.run_id) throw new Error("服务没有返回运行 ID，请先查看运行档案。");
        // The inspector reads this ID immediately, even if it has no start timestamp yet.
        if (mounted.current) navigate({ page: "runs", ...selection, run_id: result.run_id });
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { pending.current = false; setBusy(undefined); }
  }

  return <div className="wb-stack wb-config">
    <Panel title="任务与隔离环境">
      <Notice {...task} />
      <strong>{scenario?.title ?? selection.task_id}</strong>
      <p className="eyebrow">{selection.library_id} / {selection.task_id} · r{selection.revision}</p>
      {baseline && <p>对比基线 · <SearchLink to={{ page: "runs", run_id: baseline }}>{baseline}</SearchLink></p>}
      {scenario && <><p>{scenario.description}</p><Data value={scenario.environment} />
        {Object.entries(scenario.cases).map(([id, instance]) => <details key={id}><summary>{instance.title} · 初态与焦点</summary><Data value={instance.fixture} /></details>)}
      </>}
      <p className="muted">新运行会建立独立的作品、会话和模块库，不复用旧稿、会话或人工标注。</p>
      <SearchLink to={{ page: "tasks", ...selection }}>查看任务与作者原话</SearchLink>
    </Panel>
    <Panel title="预检与启动">
      {!runnable && task.data && <p role="status" className="wb-error">不可运行 · {task.data.reason ?? "未配置 Runner 或此修订不受支持。"}</p>}
      <dl className="wb-meta">
        <dt>模型连接</dt><dd>{preview?.connection_id ?? "待预检"}</dd>
        <dt>实际模型</dt><dd>{preview?.model ?? "待预检"}</dd>
        <dt>评测器版本</dt><dd>{preview?.evaluator_version ?? "待预检"}</dd>
      </dl>
      {preview && <p role="status" className={preview.valid ? undefined : "wb-error"}>{preview.valid ? "预检通过。确认后方可开始运行。" : preview.reason ?? "预检未通过。"}</p>}
      {error && <Notice error={error} />}
      <div className="wb-actions">
        <button disabled={!runnable || Boolean(busy)} onClick={() => void perform("preview")}>{busy === "preview" ? "预检中…" : "执行预检"}</button>
        <button className="wb-primary" disabled={!runnable || !approvalToken || Boolean(busy)} onClick={() => void perform("start")}>{busy === "start" ? "提交中…" : "开始运行"}</button>
      </div>
    </Panel>
  </div>;
}
