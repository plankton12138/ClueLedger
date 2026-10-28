import { useEffect, useState } from "react";
import type { ClueDetail as ClueDetailView, ClueInference, ClueIntent, ClueMode, ClueSummary, DesignFreshness } from "../../api/clues";
import { clueRequestKey, unlinkClueDesign } from "../../api/clues";
import { errorMessage } from "../../api/errors";
import { clueDetailKey, useCluesStore } from "../../state/clues";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import ClueDesignPicker from "./ClueDesignPicker";
import ClueInferenceEditor from "./ClueInferenceEditor";
import ClueReview from "./ClueReview";
import type { ClueReviewStatus } from "../../api/clues";
import type { ClueReferenceInput } from "../../api/workspace";

const MODE: Record<ClueMode, string> = { narration: "叙述", testimony: "证词", recollection: "回忆", hypothesis: "推测", dream: "梦境", unknown: "性质未标注" };
const DESIGN: Record<DesignFreshness, string> = {
  valid: "计划来源有效",
  stale: "计划来源已修改",
  detached: "模块关联需核对",
  source_missing: "计划来源已删除",
  unavailable: "计划来源暂不可读",
  invalid_range: "计划区间需核对",
};
const REVIEW: Record<ClueReviewStatus, string> = { findings: "发现问题", no_findings: "未发现问题", incomplete: "未完成" };
const INTENT: Record<ClueIntent, string> = {
  deduce: "推出结论",
  suspect: "只引起怀疑",
  keep_open: "保留多种解释",
  undecided: "尚未决定",
};

function latestReview(detail: ClueDetailView, inferenceId: string) {
  return detail.reviews.find(item => item.inference_id === inferenceId);
}

export function clueStatus(item: Pick<ClueSummary, "lifecycle" | "freshness">): string {
  if (item.lifecycle.status === "resolved" && item.freshness !== "valid") return "回收待复核";
  return { open: "未回收", resolved: "已回收", dropped: "已废弃", unknown: "暂无正文依据" }[item.lifecycle.status];
}
interface Props {
  workspaceId: string;
  clueId: string;
  asOf: number | null;
  refreshKey?: string | number;
  onLocate?: (evidenceId: string) => void;
  onClose?: () => void;
  /** 发起核对：由上层把它变成一轮真实的只读核对请求。 */
  onReview?: (inference: ClueInference, asOf: number | null) => void;
  /** 把这条线索带进当前消息草稿；只登记标识与版本，不发送。 */
  onReference?: (reference: ClueReferenceInput, title: string) => void;
}
export default function ClueDetail({ workspaceId, clueId, asOf, refreshKey, onLocate, onClose, onReview, onReference }: Props) {
  const store = useCluesStore();
  const key = clueDetailKey(clueId, asOf);
  const detail = store.workspaceId === workspaceId ? store.details[key] : undefined;
  const error = store.workspaceId === workspaceId ? store.errors[key] : undefined;
  const [linking, setLinking] = useState(false);
  const [editor, setEditor] = useState<{ existing?: ClueInference } | null>(null);
  const [unlinking, setUnlinking] = useState("");
  const [openReview, setOpenReview] = useState("");
  const [actionError, setActionError] = useState("");
  useEffect(() => { void useCluesStore.getState().loadDetail(workspaceId, clueId, asOf); }, [workspaceId, clueId, asOf, refreshKey]);
  function referenceFor(inference?: ClueInference): ClueReferenceInput {
    const review = inference ? latestReview(detail!, inference.inference_id) : undefined;
    return {
      workspace_lineage: detail!.workspace_lineage,
      clue_id: detail!.clue_id,
      clue_revision: detail!.clue_revision,
      browse_as_of_chapter: asOf ?? detail!.as_of_chapter,
      ...(inference
        ? { inference_id: inference.inference_id, inference_revision: inference.inference_revision }
        : {}),
      ...(review ? { report_id: review.report_id } : {}),
    };
  }
  function apply(next: ClueDetailView) {
    if (useCluesStore.getState().workspaceId !== workspaceId) return;
    useCluesStore.setState(state => ({ details: { ...state.details, [key]: next } }));
  }
  async function unlink(linkId: string) {
    if (!detail || unlinking) return;
    setUnlinking(linkId);
    setActionError("");
    try {
      apply(await unlinkClueDesign(workspaceId, clueId, {
        requestKey: clueRequestKey("clue-unlink"),
        expectedRevision: detail.ledger_revision,
        linkId,
        asOf,
      }));
    } catch (cause) {
      setActionError(errorMessage(cause, "移除计划关联"));
    } finally {
      setUnlinking("");
    }
  }
  if (!detail) return <div className="p-3">{error ? <StatusMessage tone="error" action={<Button size="compact" onClick={() => void store.loadDetail(workspaceId, clueId, asOf)}>重新读取</Button>}>{error}</StatusMessage> : <p className="text-sm text-sub">读取线索…</p>}{onClose && <Button size="compact" onClick={onClose}>返回线索列表</Button>}</div>;
  return <section className="clue-detail" aria-label="线索详情">
    <header className="flex flex-wrap items-start gap-2 border-b border-panel-edge p-3">
      <div className="min-w-0 flex-1"><h2 className="break-words font-serif font-bold text-fg-strong">{detail.title}</h2>
        <p className="mt-1 text-xs text-sub">{clueStatus(detail)}{detail.lifecycle.planted_chapter !== null && ` · 第 ${detail.lifecycle.planted_chapter} 章埋设`}</p></div>
      <div className="flex flex-wrap gap-2">
        {onReference && <Button size="compact" variant="quiet" onClick={() => onReference(referenceFor(), detail.title)}>带入对话</Button>}
        {onClose && <Button size="compact" variant="quiet" onClick={onClose}>返回线索列表</Button>}
      </div>
    </header>
    <div className="space-y-3 p-3 text-sm">
      {error && <StatusMessage tone="error">{error}</StatusMessage>}
      {actionError && <StatusMessage tone="error" action={<Button size="compact" onClick={() => { setActionError(""); void useCluesStore.getState().loadDetail(workspaceId, clueId, asOf); }}>重新读取</Button>}>{actionError}</StatusMessage>}
      {store.loading[key] && <p className="text-xs text-sub">正在核对当前来源…</p>}
      {detail.warnings.length > 0 && <ul className="space-y-1 text-amber">{detail.warnings.map(message => <li key={message}>{message}</li>)}</ul>}
      <div><h3 className="font-bold">正文依据</h3><p className="mt-1 text-xs text-sub">有效引文表示读者已见；证词、回忆和推测不代表客观事实。</p></div>
      {detail.observations.length === 0 ? <p className="text-sub">还没有可引用的正文记录。</p> : <ol className="space-y-3">{detail.observations.map(observation => {
        const evidence = detail.evidence.find(item => observation.evidence_ids.includes(item.evidence_id));
        if (!evidence) return null;
        return <li key={observation.observation_id} className="rounded border border-row-edge p-2">
          <div className="flex flex-wrap gap-2 text-xs text-sub"><span>第 {observation.chapter} 章 · {observation.kind === "plant" ? "埋设" : observation.kind === "resolve" ? "回收" : "再次提及"}</span><span>{MODE[observation.mode]}</span>{evidence.freshness !== "valid" && <span className="text-amber">{evidence.freshness === "stale" ? "来源已修改" : "来源不可用"}</span>}</div>
          <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-sheet-line pl-2 font-serif leading-6">{evidence.quote}</blockquote>
          <p className="my-1 truncate text-xs text-mute" title={evidence.path}>{evidence.path}</p>
          {onLocate && <Button size="compact" variant="quiet" data-source-evidence={evidence.evidence_id} onClick={() => onLocate(evidence.evidence_id)}>定位原文</Button>}
        </li>;
      })}</ol>}
      {detail.interpretation && <details><summary className="cursor-pointer text-sub">已有解释记录（待核实）</summary><p className="mt-2 whitespace-pre-wrap leading-6">{detail.interpretation}</p></details>}

      <details className="rounded border border-row-edge p-2">
        <summary className="cursor-pointer font-bold">作者计划（{detail.author_designs.length}）</summary>
        <div className="mt-2 space-y-2">
          <p className="text-xs text-sub">计划来自已装模块或作品设定，不是读者已见；模块升级或卸下后需要重新核对。</p>
          {detail.author_designs.length === 0 ? <p className="text-sub">还没有关联作者计划。</p> : <ul className="space-y-2">{detail.author_designs.map(design => (
            <li key={design.link_id} className="rounded border border-row-edge p-2">
              <div className="flex flex-wrap gap-2 text-xs text-sub">
                <span className="min-w-0 truncate" title={design.label}>{design.label}</span>
                <span className={design.freshness === "valid" ? "" : "text-amber"}>{DESIGN[design.freshness]}</span>
              </div>
              <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-sheet-line pl-2 font-serif leading-6">{design.quote}</blockquote>
              {design.relation && <p className="mt-1 text-xs text-sub">关系：{design.relation}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                {onLocate && <Button size="compact" variant="quiet" data-source-evidence={design.evidence_id} onClick={() => onLocate(design.evidence_id)}>定位计划原文</Button>}
                <Button size="compact" variant="quiet" busy={unlinking === design.link_id} onClick={() => void unlink(design.link_id)}>移除关联</Button>
              </div>
            </li>
          ))}</ul>}
          <Button size="compact" onClick={() => setLinking(true)}>关联计划</Button>
        </div>
      </details>

      <section className="space-y-2 rounded border border-row-edge p-2" aria-label="推理目标">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="min-w-0 flex-1 font-bold">推理目标</h3>
          <Button size="compact" onClick={() => setEditor({})}>新建推理</Button>
        </div>
        <p className="text-xs text-sub">目标、必要前提与其他解释是作者安排，不是正文事实；核对只依据读者已见的正文。</p>
        {detail.inferences.length === 0 ? <p className="text-sub">还没有保存推理目标。</p> : <ul className="space-y-3">{detail.inferences.map(inference => (
          <li key={inference.inference_id} className="rounded border border-row-edge p-2">
            <p className="break-words font-bold">{inference.goal}</p>
            <p className="mt-1 text-xs text-sub">{INTENT[inference.intent]} · {inference.clues.map(item => item.title).join("、")}</p>
            {inference.premises.length > 0 && <div className="mt-2"><h4 className="text-xs font-bold text-sub">必要前提</h4><ul className="mt-1 list-inside list-disc">{inference.premises.map((item, index) => <li key={`${inference.inference_id}-p-${index}`}>{item.text}</li>)}</ul></div>}
            {inference.alternatives.length > 0 && <div className="mt-2"><h4 className="text-xs font-bold text-sub">其他解释</h4><ul className="mt-1 list-inside list-disc">{inference.alternatives.map((item, index) => <li key={`${inference.inference_id}-a-${index}`}>{item.text}</li>)}</ul></div>}
            {(() => {
              const plans = detail.author_designs.filter(design => inference.design_link_ids.includes(design.link_id));
              return plans.length > 0 ? <div className="mt-2"><h4 className="text-xs font-bold text-sub">计划依据</h4><ul className="mt-1 list-inside list-disc">{plans.map(design => <li key={design.link_id}>{design.relation || design.quote}</li>)}</ul></div> : null;
            })()}
            {inference.warnings.length > 0 && <ul className="mt-2 space-y-1 text-xs text-amber">{inference.warnings.map(message => <li key={message}>{message}</li>)}</ul>}
            {latestReview(detail, inference.inference_id) && (() => {
              const review = latestReview(detail, inference.inference_id)!;
              return <p className="mt-2 text-xs text-sub">上次核对：{REVIEW[review.status]}{review.freshness !== "valid" && <span className="text-amber"> · 需复核</span>}</p>;
            })()}
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="compact" variant="quiet" onClick={() => setEditor({ existing: inference })}>编辑目标</Button>
              {onReference && <Button size="compact" variant="quiet" onClick={() => onReference(referenceFor(inference), detail.title)}>带入目标</Button>}
              {onReview && <Button size="compact" onClick={() => onReview(inference, asOf)}>核对推理</Button>}
              {latestReview(detail, inference.inference_id) && (() => {
                const review = latestReview(detail, inference.inference_id)!;
                return <Button size="compact" variant="quiet" data-review-report={review.report_id}
                  onClick={() => setOpenReview(value => value === review.report_id ? "" : review.report_id)}>
                  {openReview === review.report_id ? "收起报告" : "查看核对报告"}
                </Button>;
              })()}
            </div>
            {openReview && latestReview(detail, inference.inference_id)?.report_id === openReview && (
              <div className="mt-2"><ClueReview workspaceId={workspaceId} reportId={openReview} asOf={asOf} onLocate={onLocate} onClose={() => setOpenReview("")} /></div>
            )}
          </li>
        ))}</ul>}
      </section>
    </div>
    {linking && <ClueDesignPicker workspaceId={workspaceId} clueId={clueId} ledgerRevision={detail.ledger_revision}
      asOf={asOf} onLinked={apply} onClose={() => setLinking(false)} />}
    {editor && <AppDialog open title={editor.existing ? "编辑推理目标" : "新建推理目标"} onOpenChange={open => { if (!open) setEditor(null); }}>
      <ClueInferenceEditor workspaceId={workspaceId} clueId={clueId} asOf={asOf}
        ledgerRevision={detail.ledger_revision} existing={editor.existing}
        designs={detail.author_designs}
        onSaved={() => { setEditor(null); void useCluesStore.getState().loadDetail(workspaceId, clueId, asOf); }}
        onClose={() => setEditor(null)} />
    </AppDialog>}
  </section>;
}
