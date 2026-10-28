import { useEffect } from "react";
import type { ClueFindingKind, ClueReviewFreshness, ClueReviewStatus } from "../../api/clues";
import { clueReviewKey, useCluesStore } from "../../state/clues";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

const STATUS: Record<ClueReviewStatus, string> = {
  findings: "发现问题",
  no_findings: "未发现问题",
  incomplete: "核对未完成",
};
const FRESHNESS: Record<ClueReviewFreshness, string> = {
  valid: "依据有效",
  stale: "正文已修改，需复核",
  source_missing: "依据文件已删除",
  target_changed: "目标或计划已变化，需复核",
};
const KIND: Record<ClueFindingKind, string> = {
  inference_gap: "推理缺口",
  alternative_open: "其他解释未排除",
  premise_undisclosed: "前提尚未交代",
  counter_evidence: "反证",
};

interface Props {
  workspaceId: string;
  reportId: string;
  asOf: number | null;
  onLocate?: (evidenceId: string) => void;
  onClose?: () => void;
}

export default function ClueReview({ workspaceId, reportId, asOf, onLocate, onClose }: Props) {
  const store = useCluesStore();
  const key = clueReviewKey(reportId, asOf);
  const report = store.workspaceId === workspaceId ? store.reviews[key] : undefined;
  const error = store.workspaceId === workspaceId ? store.errors[key] : undefined;
  useEffect(() => { void useCluesStore.getState().loadReview(workspaceId, reportId, asOf); }, [workspaceId, reportId, asOf]);
  if (!report) {
    return (
      <div className="space-y-2 p-2">
        {error
          ? <StatusMessage tone="error" action={<Button size="compact" onClick={() => void useCluesStore.getState().loadReview(workspaceId, reportId, asOf)}>重新读取</Button>}>{error}</StatusMessage>
          : <p className="text-xs text-sub">正在读取核对报告…</p>}
        {onClose && <Button size="compact" variant="quiet" onClick={onClose}>收起</Button>}
      </div>
    );
  }
  return (
    <section className="space-y-2 rounded border border-row-edge p-2" aria-label="核对报告" data-review-id={report.report_id}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="min-w-0 flex-1 break-words font-bold">{report.goal}</h4>
        <span className="text-xs text-sub">{STATUS[report.status]}</span>
        {onClose && <Button size="compact" variant="quiet" onClick={onClose}>收起</Button>}
      </div>
      <p className="text-xs text-sub">
        核对截至第 {report.as_of_chapter} 章 · {report.target_paths.length} 章正文 ·
        <span className={report.freshness === "valid" ? "" : " text-amber"}> {FRESHNESS[report.freshness]}</span>
      </p>
      {report.reasons.map(reason => <p key={reason} className="text-xs text-amber">{reason}</p>)}
      {report.later_chapters > 0 && <p className="text-xs text-sub">本次核对未覆盖第 {report.as_of_chapter} 章之后的 {report.later_chapters} 章。</p>}
      {report.truncated && <p className="text-xs text-amber">当前查看范围早于核对范围，只显示当时可见的部分。</p>}

      {report.findings.length > 0 && <ol className="space-y-3">
        {report.findings.map((finding, index) => (
          <li key={`${report.report_id}-${index}`} className="rounded border border-row-edge p-2">
            <div className="flex flex-wrap gap-2 text-xs text-sub">
              <span>{KIND[finding.kind] ?? finding.kind}</span>
              <span>{finding.certainty === "supported" ? "正文支持" : "疑点"}</span>
            </div>
            <p className="mt-1 whitespace-pre-wrap leading-6">{finding.description}</p>
            <p className="mt-1 text-xs text-sub">建议：{finding.suggestion}</p>
            <ul className="mt-2 space-y-2">
              {finding.evidence.map((evidence, row) => (
                <li key={`${report.report_id}-${index}-${row}`}>
                  <blockquote className="whitespace-pre-wrap border-l-2 border-sheet-line pl-2 font-serif leading-6">{evidence.quote}</blockquote>
                  <p className="my-1 truncate text-xs text-mute" title={evidence.path}>{evidence.path}</p>
                  {onLocate && evidence.evidence_id && (
                    <Button size="compact" variant="quiet" data-source-evidence={evidence.evidence_id}
                      onClick={() => onLocate(evidence.evidence_id!)}>定位原文</Button>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>}

      {report.status === "no_findings" && <p className="text-sm text-sub">冻结目标的全文都已读过，没有发现需要处理的问题。</p>}
      {report.uncovered.length > 0 && <div>
        <h5 className="text-xs font-bold text-sub">未覆盖</h5>
        <ul className="mt-1 list-inside list-disc text-xs text-sub">{report.uncovered.map(item => <li key={item}>{item}</li>)}</ul>
      </div>}

      <details>
        <summary className="cursor-pointer text-xs text-sub">核对范围（{report.coverage.length}）</summary>
        <ul className="mt-1 space-y-1 text-xs">
          {report.coverage.map(row => (
            <li key={row.path} className="flex flex-wrap gap-2">
              <span className="min-w-0 flex-1 truncate" title={row.path}>{row.path}</span>
              <span className={row.complete ? "text-sub" : "text-amber"}>
                {row.complete ? "已读全文" : `${row.read_chars}/${row.total_chars} 字符`}
              </span>
              {row.note && <span className="basis-full text-amber">{row.note}</span>}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
