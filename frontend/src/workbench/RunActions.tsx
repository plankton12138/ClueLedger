import { useState } from "react";
import { type Rule, type Run, type RunDetail, runUrl, url, useResource } from "./api";
import { evidenceValue, versionLabel } from "./Artifacts";
import { GraderRule } from "./Tasks";
import { Data, Notice, SearchLink, Verdict } from "./ui";

export type ReviewInput = { verdict: string; reason: string; evidence_refs: string[] };

export function ReviewPanel({ detail, historical, busy, save }: {
  detail: RunDetail; historical: boolean; busy: boolean; save: (digest: string, checks: Record<string, ReviewInput>) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, Partial<ReviewInput>>>({});
  const inputs = Object.entries(detail.review_inputs);
  const digest = detail.evidence_sha256;
  const version = detail.evidence_version ?? (detail.evidence_versions.includes("legacy-current") ? "legacy-current" : "");
  const evidence = useResource<Rule>(!historical && digest && inputs.length ? url(runUrl(detail.run.run_id, "/evidence"), { version }) : null);
  if (historical) return <p className="muted">历史报告只读；切换到当前报告以查看或提交人工复核。</p>;
  const report = detail.report;
  const editable = Boolean(digest && detail.run.task && detail.run.execution_status !== "running" && report?.verdict !== "invalid");
  const edited = inputs.filter(([id]) => drafts[id]?.verdict || drafts[id]?.reason?.trim());
  const complete = edited.length > 0 && edited.every(([id]) => drafts[id].verdict && drafts[id].reason?.trim());
  function update(id: string, patch: Partial<ReviewInput>) {
    setDrafts(previous => ({ ...previous, [id]: { ...previous[id], ...patch } }));
  }

  async function submit() {
    if (!digest || !editable || !complete || !evidence.data || busy) return;
    const checks = Object.fromEntries(edited.map(([id, input]) => [id, {
      verdict: drafts[id].verdict!, reason: drafts[id].reason!.trim(), evidence_refs: input.evidence_refs,
    }]));
    await save(digest, checks);
  }

  return <>
    {digest && <div className="wb-target"><strong>当前证据摘要</strong><p className="eyebrow">{digest}</p></div>}
    {!inputs.length && <p className="muted">没有可逐项复核的条目。若旧档仅有总体确认，不补写逐项理由。</p>}
    <Notice {...evidence} />
    {evidence.error && <button className="wb-fit" onClick={evidence.refresh}>重新读取指定证据</button>}
    {inputs.map(([id, input]) => {
      const current = report?.checks.find(check => check.check_id === id);
      return <article key={id}>
        <div className="wb-run-line"><h3>{id}</h3>{current && <Verdict value={current.verdict} />}</div>
        <GraderRule rule={input.rule} />
        {current?.reason && <p className="muted">当前理由 · {current.reason}</p>}
        {input.evidence_refs.map(ref => <details key={ref}><summary>指定证据 · {ref}</summary>
          <Data value={evidence.data ? evidenceValue(evidence.data, ref) : undefined} />
        </details>)}
        {editable && <>
          <label>结论 · {id}<select disabled={busy} value={drafts[id]?.verdict ?? ""} onChange={e => update(id, { verdict: e.target.value })}>
            <option value="">不修改此项</option><option value="pass">通过</option><option value="fail">不通过</option><option value="unknown">无法判断</option>
          </select></label>
          <label>理由 · {id}<textarea rows={3} disabled={busy} value={drafts[id]?.reason ?? ""} onChange={e => update(id, { reason: e.target.value })} /></label>
        </>}
      </article>;
    })}
    {editable && inputs.length > 0 && <>
      <p className="muted">只提交修改的检查项，每项需要结论与理由。未确认的必过项不会变为通过。</p>
      <button className="wb-primary wb-fit" disabled={!complete || !evidence.data || busy} onClick={() => void submit()}>提交人工复核</button>
    </>}
  </>;
}

type Comparison = { baseline: Run; current: Run; differences: { field: string; baseline: unknown; current: unknown }[]; comparable: boolean };
const differenceLabels: Record<string, string> = {
  task: "任务修订", model: "实际模型", connection_id: "模型连接", initial_sha256: "初态摘要", evaluator_version: "评测器版本", product_version: "产品版本",
};

export function ComparisonPanel({ detail, options, busy, regrade }: {
  detail: RunDetail; options: Run[]; busy: boolean; regrade: (version: string) => Promise<void>;
}) {
  const [selected, select] = useState(detail.baseline_id ?? detail.run.baseline_id ?? "");
  const [manual, setManual] = useState("");
  const [version, setVersion] = useState("");
  const id = detail.run.run_id;
  const baseline = selected && selected !== id ? selected : "";
  const comparison = useResource<Comparison>(baseline ? url(runUrl(id, "/compare"), { baseline_id: baseline }) : null);
  const other = comparison.data?.baseline.task;
  const task = detail.run.task;
  const bestBaseline = baseline ? (task && other?.library_id === task.library_id && other?.task_id === task.task_id && other?.revision === task.revision ? baseline : undefined) : id;
  const choices = new Map(options.filter(run => run.run_id !== id).map(run => [run.run_id, run.title ?? run.run_id]));
  if (selected && selected !== id && !choices.has(selected)) choices.set(selected, comparison.data?.baseline.title ?? selected);
  const regradeVersion = version || detail.evidence_version || [...detail.evidence_versions].reverse()[0] || "";
  return <>
    <h3>指定对比基线</h3>
    <label>对比基线<select value={baseline} onChange={e => select(e.target.value)}>
      <option value="">未选择</option>{[...choices].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
    </select></label>
    <form className="wb-actions" onSubmit={e => { e.preventDefault(); if (manual.trim() && manual.trim() !== id) select(manual.trim()); }}>
      <label>基线运行 ID<input value={manual} onChange={e => setManual(e.target.value)} /></label>
      <button disabled={!manual.trim() || manual.trim() === id}>按 ID 对比</button>
    </form>
    <Notice {...comparison} />
    {comparison.data && <>
      <p className="muted">{comparison.data.comparable ? "服务未发现配置差异。" : "存在差异或缺少配置记录，不能视为可比复跑。"}</p>
      <div className="wb-table"><table>
        <thead><tr><th scope="col">项目</th><th scope="col">基线 · <SearchLink to={{ page: "runs", run_id: baseline }}>{comparison.data.baseline.title ?? baseline}</SearchLink></th><th scope="col">本轮</th></tr></thead>
        <tbody>
          <tr><th scope="row">业务判定</th><td><Verdict value={comparison.data.baseline.verdict} /></td><td><Verdict value={comparison.data.current.verdict} /></td></tr>
          {comparison.data.differences.map(row => <tr key={row.field}><th scope="row">{differenceLabels[row.field] ?? row.field}</th><td><Data value={row.baseline} /></td><td><Data value={row.current} /></td></tr>)}
        </tbody>
      </table></div>
    </>}
    <article><h3>重新运行 · 新 Trial</h3>
      <p>重新运行会进行预检，另建隔离作品和会话，不继承任何人工标注。</p>
      {task && bestBaseline ? <SearchLink to={{ page: "launch", ...task, baseline_id: bestBaseline }}>按此配置另起复跑</SearchLink>
        : <p className="muted">仅同一任务修订可作为新运行的基线。</p>}
    </article>
    <article><h3>用归档证据重判 · 同一运行</h3>
      <p>不调用模型，不新增运行。报告保留在同一运行下。</p>
      {task && detail.evidence_versions.length > 0 && detail.run.execution_status !== "running" ? <>
        <label>重判证据版本<select value={regradeVersion} disabled={busy} onChange={e => setVersion(e.target.value)}>
          {detail.evidence_versions.map(v => <option key={v} value={v}>{versionLabel(v)}</option>)}
        </select></label>
        <button className="wb-fit" disabled={busy || !regradeVersion} onClick={() => void regrade(regradeVersion)}>重判同一运行</button>
      </> : <p className="muted">当前没有可重判的任务证据。</p>}
    </article>
  </>;
}
