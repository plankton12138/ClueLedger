import { useEffect, useState } from "react";
import { Button } from "./ui/button";
import AgentOutputView from "./AgentOutputView";

type Json = Record<string, unknown>;
interface Job { job_id: string; job_type: string; state: string; output: Json | null; acceptance?: { commit_id: string } }
interface Story { commit_id: string | null; artifacts: { artifact_id: string; kind: string; content: Json }[] }
const states: Record<string, string> = { queued: "排队中", running: "执行中", waiting: "等待审阅", retryable_failed: "故障待重试", terminal_failed: "需要处理", completed: "任务完成", cancelled: "已取消" };
const roles: Record<string, string> = { planner: "策划", writer: "写作", reviser: "修订", auditor: "审计", style_observer: "风格观察", coordinator: "主编", "manuscript.analysis": "编辑分析派发" };
const eventLabels: Record<string, string> = { "job.queued": "已排队", "job.running": "开始执行", "job.lease_acquired": "已取得执行权", "job.heartbeat": "执行仍在继续", "job.recovered": "已恢复中断任务", "agent.started": "工作者已开始", "agent.schema_correction": "已要求纠正候选", "agent.tool_execution_start": "正在查询资料或校验候选", "agent.tool_execution_end": "查询或校验结束", "agent.turn_end": "本轮推理结束", "agent.output.proposed": "已收到结构化候选", "agent.tool.denied": "已阻止范围外操作", "job.waiting": "等待作者处理", "job.completed": "任务完成", "job.cancelled": "任务已取消", "job.retryable_failed": "环境故障，等待重试", "job.terminal_failed": "需要处理失败原因", "job.analysis_recorded": "分析结果已记录" };
async function api<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ? `${data.error.why}；${data.error.fix}` : JSON.stringify(data.detail));
  return data as T;
}

export default function AgentWorkbench() {
  const [project, setProject] = useState(() => localStorage.getItem("hastings.v2.project") ?? "");
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [story, setStory] = useState<Story>({ commit_id: null, artifacts: [] });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [beat, setBeat] = useState("");
  const [chapter, setChapter] = useState("");
  const [text, setText] = useState("");
  const [chapterBase, setChapterBase] = useState("");
  const [events, setEvents] = useState<Json[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [proof, setProof] = useState("");
  const prefix = `/api/v2/projects/${encodeURIComponent(project)}`;
  const refresh = async () => {
    const [next, tasks] = await Promise.all([api<Story>(`${prefix}/story`), api<{ jobs: Job[] }>(`${prefix}/agent-jobs`)]);
    setStory(next); setJobs(tasks.jobs);
  };
  useEffect(() => {
    if (!project) return;
    let disposed = false;
    const poll = async () => {
      try {
        const tasks = await api<{ jobs: Job[] }>(`${prefix}/agent-jobs`);
        if (!disposed) setJobs(tasks.jobs);
      } catch { /* 主动载入时显示错误，轮询不覆盖作者正在看的结果。 */ }
    };
    const timer = setInterval(() => void poll(), 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [project, prefix]);
  const perform = async (work: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const submit = async (role: string) => {
    const cid = chapter || `${project}:manuscript-${crypto.randomUUID()}`;
    if (role === "writer") setChapter(cid);
    await api(`${prefix}/agent-jobs`, { role, instruction, target_ids: role === "writer" ? [beat] : [], chapter_id: cid, idempotency_key: crypto.randomUUID() });
    await refresh();
  };
  const field = "rounded border border-line bg-ink px-3 py-2 text-paper";
  return <section aria-label="Agent 创作任务" className="my-4 space-y-4 border border-line p-4">
    <h2 className="text-lg text-gold">从规划到一章</h2>
    <p className="text-sm text-paper/60">先接受故事节拍，再生成正文。任务完成表示模型已返回结果；正式稿以接受后的提交编号为准。</p>
    <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); void perform(async () => {
      const created = await api<{ project_id: string }>("/api/v2/projects", { name });
      setProject(created.project_id); localStorage.setItem("hastings.v2.project", created.project_id);
      setJobs([]); setStory({ commit_id: null, artifacts: [] }); setProof("");
    }); }}>
      <input aria-label="新故事名称" className={field} value={name} onChange={e => setName(e.target.value)} required placeholder="故事名称" />
      <Button type="submit" disabled={busy}>新建 V2 故事</Button>
    </form>
    <div className="flex flex-wrap gap-2">
      <input aria-label="创作项目编号" className={`${field} min-w-64 flex-1`} value={project} onChange={e => { setProject(e.target.value); setJobs([]); setStory({ commit_id: null, artifacts: [] }); setEvents([]); setChapter(""); setText(""); setProof(""); }} />
      <Button disabled={busy || !project} onClick={() => void perform(async () => { localStorage.setItem("hastings.v2.project", project); await refresh(); })}>载入创作任务</Button>
    </div>
    {error && <p role="alert" className="text-overdue">{error}</p>}
    {proof && <p role="status" className="break-all text-planted">已提交：{proof}</p>}
    <label className="block">创作要求<textarea aria-label="创作要求" className={`${field} mt-1 w-full`} value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="描述故事方向、这一章的目的或希望检查的问题" /></label>
    <div className="flex flex-wrap gap-2">
      <Button disabled={busy || !project || !instruction.trim()} onClick={() => void perform(() => submit("planner"))}>提出故事规划</Button>
      <select aria-label="已接受节拍" className={field} value={beat} onChange={e => setBeat(e.target.value)}><option value="">选择已接受节拍</option>{story.artifacts.filter(a => a.kind === "narrative.beat" || a.kind === "story.plot_beat").map(a => <option key={a.artifact_id} value={a.artifact_id}>{String(a.content.title || a.content.purpose || a.artifact_id)}</option>)}</select>
      <Button disabled={busy || !beat} onClick={() => void perform(() => submit("writer"))}>生成一章候选</Button>
      <Button disabled={busy || !story.commit_id} onClick={() => void perform(() => submit("auditor"))}>检查一致性</Button>
    </div>
    {story.artifacts.length > 0 && <details><summary>已接受的故事对象（{story.artifacts.length}）</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(story.artifacts, null, 2)}</pre></details>}
    <div className="space-y-3">{jobs.filter(j => j.job_type !== "manuscript.analysis" || ["retryable_failed", "terminal_failed"].includes(j.state)).map(job => <article key={job.job_id} className="border border-line p-3" aria-label={`${roles[job.job_type] ?? job.job_type}任务`}>
      <p>{roles[job.job_type] ?? job.job_type} · <span className="text-gold">{states[job.state] ?? job.state}</span></p>
      {job.acceptance?.commit_id && <p className="break-all text-sm text-planted">正式提交：{job.acceptance.commit_id}</p>}
      {job.output && <div className="my-3 max-h-96 overflow-auto text-sm"><AgentOutputView output={job.output} names={Object.fromEntries(story.artifacts.map(a => [a.artifact_id, String(a.content.name || a.content.title || a.artifact_id)]))} /></div>}
      {job.job_type === "reviser" && job.state === "waiting" && <p className="mb-2 text-sm text-paper/60">修订候选请在下方变更中心审阅，再决定进入正式稿的部分。</p>}
      <div className="flex flex-wrap gap-2">
        {(job.state === "queued" || job.state === "retryable_failed") && <Button disabled={busy} onClick={() => void perform(async () => { await api(`${prefix}/agent-jobs/${job.job_id}/run`, {}); await refresh(); })}>运行或重试</Button>}
        {job.state === "waiting" && (job.job_type === "planner" || job.job_type === "writer") && (job.output?.kind === "story_proposal" || job.output?.kind === "manuscript_patch") && <Button disabled={busy} onClick={() => void perform(async () => {
          const current = await api<Story>(`${prefix}/story`);
          const accepted = await api<{ commit_id: string }>(`${prefix}/agent-jobs/${job.job_id}/accept`, { expected_head: current.commit_id });
          setProof(accepted.commit_id); await refresh();
        })}>接受候选并提交</Button>}
        {!["completed", "cancelled", "terminal_failed"].includes(job.state) && <Button onClick={() => void perform(async () => { await api(`${prefix}/agent-jobs/${job.job_id}/cancel`, {}); await refresh(); })}>取消任务</Button>}
        <Button onClick={() => void perform(async () => { const result = await api<{ events: Json[] }>(`${prefix}/agent-jobs/${job.job_id}/events`); setEvents(result.events); })}>查看任务过程</Button>
      </div>
    </article>)}</div>
    {events.length > 0 && <details open><summary>任务过程</summary><ol className="text-sm text-paper/70">{events.map(e => <li key={String(e.sequence)}>{String(e.sequence)} · {eventLabels[String(e.type)] || "任务进度已更新"}</li>)}</ol></details>}
    <details><summary>人工修改正文并学习</summary><div className="mt-3 space-y-2">
      <input aria-label="编辑章节编号" className={field} value={chapter} onChange={e => { setChapter(e.target.value); setChapterBase(""); setText(""); }} placeholder="已提交的章节编号" />
      <Button disabled={busy || !chapter} onClick={() => void perform(async () => { const result = await api<{ text: string; chapter_base: string }>(`${prefix}/chapters/${encodeURIComponent(chapter)}`); setText(result.text); setChapterBase(result.chapter_base); })}>读取正文</Button>
      <textarea aria-label="人工正文" disabled={!chapterBase} className={`${field} min-h-48 w-full`} value={text} onChange={e => setText(e.target.value)} />
      <Button disabled={busy || !chapterBase} onClick={() => void perform(async () => {
        const saved = await api<{ commit_id: string }>(`${prefix}/chapters/${encodeURIComponent(chapter)}/save`, { idempotency_key: crypto.randomUUID(), full_text: text, expected_chapter_base: chapterBase });
        setProof(saved.commit_id); setChapterBase(""); await refresh();
      })}>保存人工修改</Button>
      <p className="text-xs text-paper/60">观察只会建立偏好候选；事实、数字和格式修改不会自动成为文风规则。</p>
    </div></details>
  </section>;
}
