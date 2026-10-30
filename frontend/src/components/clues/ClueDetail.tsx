import { useEffect } from "react";
import type { ClueDetail as ClueDetailView, ClueMode, ClueSummary } from "../../api/clues";
import { clueDetailKey, useCluesStore } from "../../state/clues";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import type { ClueReferenceInput } from "../../api/workspace";

const MODE: Record<ClueMode, string> = { narration: "叙述", testimony: "证词", recollection: "回忆", hypothesis: "推测", dream: "梦境", unknown: "性质未标注" };

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
  /** 把这条线索带进当前消息草稿；只登记标识与版本，不发送。 */
  onReference?: (reference: ClueReferenceInput, title: string) => void;
}
export default function ClueDetail({ workspaceId, clueId, asOf, refreshKey, onLocate, onClose, onReference }: Props) {
  const store = useCluesStore();
  const key = clueDetailKey(clueId, asOf);
  const detail = store.workspaceId === workspaceId ? store.details[key] : undefined;
  const error = store.workspaceId === workspaceId ? store.errors[key] : undefined;
  useEffect(() => { void useCluesStore.getState().loadDetail(workspaceId, clueId, asOf); }, [workspaceId, clueId, asOf, refreshKey]);
  function referenceFor(detail: ClueDetailView): ClueReferenceInput {
    return {
      workspace_lineage: detail.workspace_lineage,
      clue_id: detail.clue_id,
      clue_revision: detail.clue_revision,
      browse_as_of_chapter: asOf ?? detail.as_of_chapter,
    };
  }
  if (!detail) return <div className="p-3">{error ? <StatusMessage tone="error" action={<Button size="compact" onClick={() => void store.loadDetail(workspaceId, clueId, asOf)}>重新读取</Button>}>{error}</StatusMessage> : <p className="text-sm text-sub">读取线索…</p>}{onClose && <Button size="compact" onClick={onClose}>返回线索列表</Button>}</div>;
  return <section className="clue-detail" aria-label="线索详情">
    <header className="flex flex-wrap items-start gap-2 border-b border-panel-edge p-3">
      <div className="min-w-0 flex-1"><h2 className="break-words font-serif font-bold text-fg-strong">{detail.title}</h2>
        <p className="mt-1 text-xs text-sub">{clueStatus(detail)}{detail.lifecycle.planted_chapter !== null && ` · 第 ${detail.lifecycle.planted_chapter} 章埋设`}</p></div>
      <div className="flex flex-wrap gap-2">
        {onReference && <Button size="compact" variant="quiet" onClick={() => onReference(referenceFor(detail), detail.title)}>带入对话</Button>}
        {onClose && <Button size="compact" variant="quiet" onClick={onClose}>返回线索列表</Button>}
      </div>
    </header>
    <div className="space-y-3 p-3 text-sm">
      {error && <StatusMessage tone="error">{error}</StatusMessage>}
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
    </div>
  </section>;
}
