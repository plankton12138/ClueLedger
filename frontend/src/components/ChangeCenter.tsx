import { useState } from "react";
import { Button } from "./ui/button";
import AgentWorkbench from "./AgentWorkbench";

type Json = Record<string, unknown>;
interface Hunk { hunk_id: string; artifact_id: string; field: string; old: unknown; new: unknown }
interface Change { change_set_id: string; intent: string; status: string; base_commit_id: string }
interface Detail { head_commit_id: string; change_set: Change; objects: { kind: string; content: Json }[] }

const labels: Record<string, string> = { opened: "待分析", analyzing: "分析中", analyzed: "已分析",
  approved: "已批准", executing: "执行中", review: "待审阅", committed: "已提交", failed: "执行失败", abandoned: "已放弃" };
const severityLabels: Record<string, string> = { must_fix: "必须修复", should_fix: "建议修复", review: "待复核", unaffected: "无须修改" };
const impactLabels: Record<string, string> = { open: "未解决", resolved: "已处理", accepted_risk: "接受风险", planned: "已计划", dismissed: "已排除" };
const display = (value: unknown) => typeof value === "string" ? value : Array.isArray(value) ? value.join("") : JSON.stringify(value);

async function request<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ? `${data.error.why}；${data.error.fix}` : JSON.stringify(data.detail));
  return data as T;
}

export default function ChangeCenter() {
  const [project, setProject] = useState(() => localStorage.getItem("hastings.v2.project") ?? "");
  const [changes, setChanges] = useState<Change[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [intent, setIntent] = useState("");
  const [seeds, setSeeds] = useState("");
  const [chapters, setChapters] = useState("");
  const [seedPatchText, setSeedPatchText] = useState("{}");
  const [patchText, setPatchText] = useState("{}");
  const [hunks, setHunks] = useState<Hunk[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [riskText, setRiskText] = useState("{}");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [audit, setAudit] = useState<Json | null>(null);
  const prefix = `/api/v2/projects/${encodeURIComponent(project)}`;
  const list = async () => {
    localStorage.setItem("hastings.v2.project", project);
    const data = await request<{ changes: Change[] }>(`${prefix}/changes`);
    setChanges(data.changes);
  };
  const load = async (id: string) => {
    const next = await request<Detail>(`${prefix}/changes/${encodeURIComponent(id)}`);
    setDetail(next);
    const patch = next.objects.find(o => o.kind === "changes.patch_set");
    setHunks((patch?.content.hunks as Hunk[] | undefined) ?? []);
    setSelected(new Set());
  };
  const perform = async (task: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await task(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const action = async (name: string, payload: Json = {}) => {
    if (!detail) return;
    await request(`${prefix}/changes/${encodeURIComponent(detail.change_set.change_set_id)}/${name}`,
      { idempotency_key: crypto.randomUUID(), expected_head: detail.head_commit_id, ...payload });
    await load(detail.change_set.change_set_id); await list();
  };
  const report = detail?.objects.find(o => o.kind === "changes.impact_report")?.content;
  const plan = detail?.objects.find(o => o.kind === "changes.revision_plan")?.content;
  const items = (report?.items ?? []) as { target_artifact_id: string; severity: string; reason: string; status: string; path: { source: string; relation_type: string; target: string }[] }[];
  const status = detail?.change_set.status;
  const inputClass = "rounded border border-line bg-ink px-3 py-2 text-paper";

  return <main className="flex-1 overflow-auto p-6 text-paper" aria-label="变更中心">
    <h1 className="mb-2 text-xl text-gold">变更中心</h1>
    <p className="mb-4 text-sm text-paper/60">查看事实变化的影响，在修订分支试改，再决定哪些内容进入正式稿。</p>
    <details className="mb-4"><summary className="cursor-pointer text-gold">规划与生成任务</summary><AgentWorkbench /></details>
    <form className="mb-5 flex gap-2" onSubmit={e => { e.preventDefault(); setDetail(null); void perform(list); }}>
      <label>项目 <input className={inputClass} value={project} disabled={busy} onChange={e => { setProject(e.target.value); setDetail(null); setChanges([]); setAudit(null); }} required placeholder="选择已有 V2 项目的编号" /></label>
      <Button type="submit" disabled={busy || !project}>载入变更</Button>
    </form>
    {error && <p role="alert" className="mb-4 border border-overdue p-3 text-overdue">{error}</p>}
    <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
      <aside className="space-y-3">
        {changes.map(c => <button key={c.change_set_id} className="block w-full border border-line p-3 text-left"
          disabled={busy} onClick={() => void perform(() => load(c.change_set_id))}>
          <span className="text-xs text-gold">{labels[c.status] ?? c.status}</span><p>{c.intent}</p>
        </button>)}
        <details className="border border-line p-3"><summary>发起变化</summary>
          <form className="mt-3 flex flex-col gap-2" onSubmit={e => { e.preventDefault(); void perform(async () => {
            const manuscript = await request<{ commit_id: string }>(`${prefix}/manuscript`);
            const opened = await request<Change>(`${prefix}/changes`, { idempotency_key: crypto.randomUUID(), intent,
              base_commit_id: manuscript.commit_id, seed_ids: seeds.split(",").map(s => s.trim()).filter(Boolean),
              seed_patches: JSON.parse(seedPatchText),
              scope: { roots: chapters.split(",").map(s => s.trim()).filter(Boolean) } });
            await list(); await load(opened.change_set_id);
          }); }}>
            <label>为什么修改<textarea className={`${inputClass} w-full`} value={intent} onChange={e => setIntent(e.target.value)} required /></label>
            <label>变化对象<input className={`${inputClass} w-full`} value={seeds} onChange={e => setSeeds(e.target.value)} required placeholder="对象编号，逗号分隔" /></label>
            <label>章节范围<input className={`${inputClass} w-full`} value={chapters} onChange={e => setChapters(e.target.value)} placeholder="留空检查全书" /></label>
            <label>期望的新内容<textarea className={`${inputClass} w-full font-mono text-xs`} value={seedPatchText} onChange={e => setSeedPatchText(e.target.value)} placeholder="对象编号对应的完整内容；{} 表示仅分析影响" /></label>
            <Button type="submit" disabled={busy}>建立变更集</Button>
          </form>
        </details>
      </aside>
      <section className="min-w-0 space-y-4">
        {!detail ? <p>选择一个变更集，查看影响与下一步。</p> : <>
          <header><span className="text-gold">{labels[status ?? ""]}</span><h2 className="text-lg">{detail.change_set.intent}</h2>
            <p className="break-all text-xs text-paper/50">分析依据：{detail.change_set.base_commit_id}</p></header>
          <div className="flex flex-wrap gap-2">
            {status === "opened" && <Button disabled={busy} onClick={() => void perform(() => action("analyze"))}>分析影响</Button>}
            {status === "analyzed" && !plan && <Button disabled={busy} onClick={() => void perform(() => action("plan"))}>制定修订计划</Button>}
            {status === "analyzed" && plan && <Button disabled={busy} onClick={() => void perform(() => action("approve"))}>批准所示范围与计划</Button>}
            {(status === "approved" || status === "executing") && <Button disabled={busy} onClick={() => void perform(() => action("execute", { patches: JSON.parse(patchText) }))}>在分支执行修订</Button>}
            {(status === "approved" || status === "executing") && <Button disabled={busy} onClick={() => void perform(() => action("execute-agent"))}>由 Agent 执行已批准修订</Button>}
            {status === "review" && <Button disabled={busy} onClick={() => void perform(() => action("review"))}>生成审阅差异</Button>}
            <Button disabled={busy} variant="outline" onClick={() => void perform(async () => setAudit(await request<Json>(`${prefix}/audit`, { idempotency_key: crypto.randomUUID() })))}>检查故事一致性</Button>
          </div>
          {plan && <div className="border border-line p-3 text-sm"><h3 className="text-gold">{status === "analyzed" ? "批准前确认" : "已批准的范围与计划"}</h3>
            <p>原因：{detail.change_set.intent}。预计修订 {(plan.steps as unknown[]).length} 组；必须处理 {items.filter(i => i.severity === "must_fix").length} 项。</p>
            {(plan.steps as { step_id: string; title: string; target_artifact_ids: string[] }[]).map(step => <p key={step.step_id}>{step.title}：{step.target_artifact_ids.join("、")}</p>)}
            <p>接受风险：{JSON.stringify(plan.risk_acceptances ?? [])}</p>
          </div>}
          {items.map((item, i) => <article key={i} className="border-l-2 border-gold/40 pl-3 text-sm">
            <p>{item.target_artifact_id} · {severityLabels[item.severity] ?? item.severity} · {impactLabels[item.status] ?? item.status}</p><p className="text-paper/60">{item.reason}</p>
            <p className="break-all text-xs text-paper/45">{item.path.map(p => `${p.source} → ${p.relation_type} → ${p.target}`).join(" / ")}</p>
          </article>)}
          {(status === "approved" || status === "executing") && <details><summary>提供候选修订内容</summary>
            <p className="text-sm text-paper/60">本阶段由人工提供候选稿；填写对象编号对应的完整新内容，系统仍会检查范围。</p>
            <textarea aria-label="候选修订内容" className={`${inputClass} h-40 w-full font-mono text-xs`} value={patchText} onChange={e => setPatchText(e.target.value)} />
          </details>}
          {status === "review" && hunks.map(h => <article key={h.hunk_id} className="border border-line p-3">
            <label><input type="checkbox" checked={selected.has(h.hunk_id)} onChange={e => setSelected(old => {
              const next = new Set(old); if (e.target.checked) next.add(h.hunk_id); else next.delete(h.hunk_id); return next;
            })} /> 接受 {h.artifact_id} / {h.field}</label>
            <div className="grid gap-3 sm:grid-cols-2"><pre className="whitespace-pre-wrap text-overdue">{display(h.old)}</pre><pre className="whitespace-pre-wrap text-planted">{display(h.new)}</pre></div>
          </article>)}
          {status === "review" && <><details><summary>记录接受的风险</summary><textarea aria-label="接受风险及原因" className={`${inputClass} w-full font-mono`} value={riskText} onChange={e => setRiskText(e.target.value)} /><p>填写对象编号与保留原因。</p></details>
            <p className="text-sm">已选择 {selected.size} 处。未选择的修改保留在分支中，对应影响仍保持开放。</p>
            <Button disabled={busy} onClick={() => void perform(() => action("merge", { selected: [...selected], risks: JSON.parse(riskText) }))}>提交所选修改</Button></>}
          {audit && <section><h3 className="text-gold">审计结果</h3><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(audit.findings, null, 2)}</pre></section>}
        </>}
      </section>
    </div>
  </main>;
}
