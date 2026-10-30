import { useEffect } from "react";
import { storyDetailKey, storyListKey, useCluesStore } from "../../state/clues";
import type { ClueReferenceInput } from "../../api/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

interface Props {
  workspaceId: string;
  variantId: string;
  nodeId: string | null;
  asOf: number | null;
  active: boolean;
  nodeTitle: (nodeId: string) => string;
  onLocateNode: (nodeId: string) => void;
  onLocateEvidence: (evidenceId: string, asOf: number) => void;
  onReference: (reference: ClueReferenceInput, title: string) => void;
}

const RESOLUTION = { explain: "计划解释", leave_open: "计划保留开放", undecided: "回收未定" };

export default function StoryClues({
  workspaceId, variantId, nodeId, asOf, active, nodeTitle, onLocateNode, onLocateEvidence, onReference,
}: Props) {
  const clues = useCluesStore();
  const listKey = storyListKey(variantId, nodeId, asOf);
  const data = clues.workspaceId === workspaceId ? clues.lists[listKey] : undefined;
  const error = clues.workspaceId === workspaceId ? clues.errors[listKey] : undefined;
  const selectedId = clues.selected[listKey] ?? null;
  const detailKey = selectedId ? storyDetailKey(variantId, selectedId, asOf) : "";
  const detail = selectedId && clues.workspaceId === workspaceId ? clues.details[detailKey] : undefined;
  const detailError = selectedId && clues.workspaceId === workspaceId ? clues.errors[detailKey] : undefined;

  useEffect(() => {
    if (active) void useCluesStore.getState().loadStoryList(workspaceId, variantId, nodeId, asOf);
  }, [workspaceId, variantId, nodeId, asOf, active]);
  useEffect(() => {
    if (active && selectedId) void useCluesStore.getState().loadStoryDetail(workspaceId, variantId, selectedId, asOf);
  }, [workspaceId, variantId, selectedId, asOf, active]);

  if (!active) return null;

  if (selectedId) return <section className="flex h-full min-h-0 flex-col" aria-label="全书线索">
    <header className="flex flex-none flex-wrap items-start gap-2 border-b border-panel-edge p-3">
      <div className="min-w-0 flex-1">
        <h2 className="break-words font-serif font-bold">{detail?.title ?? "线索"}</h2>
        <p className="mt-1 text-xs text-sub">{nodeId ? `与「${nodeTitle(nodeId)}」相关` : "全书线索"}</p>
      </div>
      <Button size="compact" variant="quiet" onClick={() => clues.select(listKey, null)}>返回列表</Button>
    </header>
    {detailError && <StatusMessage tone="error">{detailError}</StatusMessage>}
    {!detail ? <p className="p-3 text-sm text-sub">读取线索…</p> : <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3 text-sm">
      <section className="space-y-2">
        <h3 className="font-bold">作者计划</h3>
        {detail.plan ? <>
          <p className="text-sub">{detail.plan.intention || "创作意图未写。"}</p>
          <p className="text-xs text-sub">{RESOLUTION[detail.plan.resolution_intent]}</p>
          {detail.plan.author_truth && <details><summary className="cursor-pointer text-xs text-sub">作者真相</summary>
            <p className="mt-2 whitespace-pre-wrap leading-6">{detail.plan.author_truth}</p></details>}
        </> : <p className="text-sub">这条线索还没有构思计划。</p>}
      </section>
      <section className="space-y-2">
        <h3 className="font-bold">使用位置</h3>
        {detail.uses.length === 0 ? <p className="text-sub">构思里还没有使用这条线索。</p> : <ul className="space-y-2">
          {detail.uses.map(use => <li key={`${use.node_id}-${use.presentation}`} className="rounded border border-row-edge p-2">
            <button type="button" className="text-left font-bold underline-offset-2 hover:underline"
              onClick={() => onLocateNode(use.node_id)}>{nodeTitle(use.node_id)}</button>
            <p className="mt-1 whitespace-pre-wrap leading-6">{use.presentation}</p>
            <p className="mt-1 text-xs text-sub">{use.roles.length > 0 ? use.roles.join("、") : "作用未定"}
              {use.expected_interpretation && ` · 想引导：${use.expected_interpretation}`}</p>
          </li>)}
        </ul>}
      </section>
      <section className="space-y-2">
        <h3 className="font-bold">正文依据</h3>
        {detail.observations.length === 0 ? <p className="text-sub">还没有可引用的正文记录。</p> : <ol className="space-y-3">
          {detail.observations.map(observation => {
            const evidence = detail.evidence.find(item => observation.evidence_ids.includes(item.evidence_id));
            if (!evidence) return null;
            return <li key={observation.observation_id} className="rounded border border-row-edge p-2">
              <div className="flex flex-wrap gap-2 text-xs text-sub"><span>第 {observation.chapter} 章</span>
                <span>{observation.kind === "plant" ? "埋设" : observation.kind === "resolve" ? "回收" : "再次提及"}</span>
                {evidence.freshness !== "valid" && <span className="text-amber">来源需复核</span>}</div>
              <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-sheet-line pl-2 font-serif leading-6">{evidence.quote}</blockquote>
              <Button size="compact" variant="quiet" onClick={() => onLocateEvidence(evidence.evidence_id, detail.as_of_chapter)}>定位原文</Button>
            </li>;
          })}
        </ol>}
      </section>
      <Button size="compact" variant="quiet" onClick={() => onReference({
        workspace_lineage: detail.workspace_lineage,
        clue_id: detail.clue_id,
        clue_revision: detail.clue_revision,
        browse_as_of_chapter: asOf ?? detail.as_of_chapter,
      }, detail.title)}>带入对话</Button>
    </div>}
  </section>;

  return <section className="flex h-full min-h-0 flex-col" aria-label="全书线索">
    <header className="flex flex-none flex-wrap items-center gap-2 border-b border-panel-edge p-3">
      <h2 className="min-w-0 flex-1 truncate font-bold">{nodeId ? `与「${nodeTitle(nodeId)}」相关` : "全书线索"}</h2>
      <p className="basis-full text-xs text-sub">作者计划与正文观察分开显示。</p>
    </header>
    {error && <StatusMessage tone="error" action={<Button size="compact" onClick={() => void clues.loadStoryList(workspaceId, variantId, nodeId, asOf)}>重新读取</Button>}>{error}</StatusMessage>}
    <div className="min-h-0 flex-1 overflow-auto">
      {!data ? <p className="p-3 text-sm text-sub">读取全书线索…</p> : <>
        <ul className="divide-y divide-row-edge">{data.items.map(item => <li key={item.clue_id}>
          <button className="w-full px-3 py-3 text-left hover:bg-ui-selected" onClick={() => clues.select(listKey, item.clue_id)}>
            <span className="block break-words text-sm font-bold">{item.title}</span>
            <span className="mt-1 block text-xs text-sub">{item.plan ? RESOLUTION[item.plan.resolution_intent] : "尚无计划"}
              {item.plan?.intention && ` · ${item.plan.intention}`}</span>
          </button>
        </li>)}</ul>
        {data.items.length === 0 && <p className="p-3 text-sm text-sub">{nodeId ? "这个节点还没有使用线索。" : "构思里还没有线索。"}</p>}
        {data.next_cursor && <Button className="m-3" size="compact" disabled={clues.loading[listKey]}
          onClick={() => void clues.loadStoryList(workspaceId, variantId, nodeId, asOf, true)}>更多线索</Button>}
      </>}
    </div>
  </section>;
}
