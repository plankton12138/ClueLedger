import { useEffect, useState } from "react";
import { clueListKey, useCluesStore } from "../../state/clues";
import { useMaterialTasksStore } from "../../state/materialTasks";
import { navigate } from "../../state/navigation";
import { useModelsStore } from "../../state/models";
import { useWorkspaceStore } from "../../state/workspace";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import MaterialTaskBar from "../materials/MaterialTaskBar";
import ClueDetail, { clueStatus } from "./ClueDetail";
import type { ClueInference } from "../../api/clues";
import type { ClueReferenceInput } from "../../api/workspace";

interface Props {
  workspaceId: string;
  chapterPath: string | null;
  asOf: number | null;
  active: boolean;
  onLocate: (id: string, asOf: number) => void;
  onReview?: (inference: ClueInference, asOf: number | null) => void;
  onReference?: (reference: ClueReferenceInput, title: string) => void;
}
export default function ChapterClues({ workspaceId, chapterPath, asOf, active, onLocate, onReview, onReference }: Props) {
  const clues = useCluesStore();
  const [starting, setStarting] = useState<string | null>(null);
  const tasks = useMaterialTasksStore();
  const fileDigest = useWorkspaceStore(state => state.file?.digest);
  const writing = useConversationsStore(runningConversation);
  const key = clueListKey(chapterPath, asOf);
  const data = clues.workspaceId === workspaceId ? clues.lists[key] : undefined;
  const error = clues.workspaceId === workspaceId ? clues.errors[key] : undefined;
  const selected = data?.items.find(item => item.clue_id === clues.selected[key]);
  const owner = tasks.workspaceId === workspaceId;
  const task = owner ? tasks.order.map(id => tasks.tasks[id]).find(item => item.domain === "clues" && chapterPath && item.scope.chapter_paths.includes(chapterPath)) : undefined;
  const running = task?.status === "accepted" || task?.status === "running";
  const occupied = Boolean(writing || owner && Object.keys(tasks.active).length || starting === workspaceId);
  useEffect(() => { if (active) void useMaterialTasksStore.getState().load(workspaceId, "clues"); }, [workspaceId, active]);
  useEffect(() => { if (active && chapterPath) void useCluesStore.getState().loadList(workspaceId, chapterPath, asOf); }, [workspaceId, chapterPath, asOf, active, tasks.revision, fileDigest]);
  if (!active) return null;
  const chapter = data?.chapter;
  const coverage = chapter?.coverage;
  const stale = coverage && coverage.freshness !== "valid" && coverage.outcome !== "unrecorded";
  const status = !chapterPath ? "选择一章正文，查看与本章相关的线索。" : !data ? "正在读取本章线索…"
    : !chapter ? "当前查看范围尚未包含本章。"
    : !chapter.has_content ? "这一章还没有可整理的正文。"
    : stale ? "正文已变化或暂不可读，已有整理结果需要重新核对。"
    : coverage?.outcome === "unrecorded" ? "本章尚未整理。已有跨章线索仍可查看。"
    : coverage?.outcome === "empty" ? "已整理，未发现相关线索。"
    : coverage?.outcome === "insufficient" ? "本章信息不足，尚不能形成完整线索记录。"
    : "";
  async function organize() {
    if (!chapter?.digest || !chapter.has_content || occupied) return;
    const models = useModelsStore.getState();
    if (!models.selection.available) { models.openSettings(); return; }
    setStarting(workspaceId);
    try {
      await useMaterialTasksStore.getState().start(workspaceId, {
        domain: "clues", chapter_paths: [chapter.path], entity_paths: [],
        as_of_chapter: data!.as_of_chapter, observed_digests: { [chapter.path]: chapter.digest },
      }, "本章线索");
    } finally { setStarting(value => value === workspaceId ? null : value); }
  }
  return <section className="flex h-full min-h-0 flex-col" aria-label="本章线索">
    <header className="flex flex-none flex-wrap items-center gap-2 border-b border-panel-edge p-3">
      <h2 className="min-w-0 flex-1 truncate font-bold" title={chapter?.title}>{chapter?.title ?? "本章线索"}</h2>
      <Button size="compact" variant="quiet" onClick={() => navigate({ kind: "materials", workspaceId, section: "clues", chapter: data?.as_of_chapter ?? asOf, order: "chapter", entryId: selected?.clue_id })}>全部线索</Button>
      {chapter && <p className="basis-full text-xs text-sub">查看截至第 {data?.as_of_chapter} 章</p>}
    </header>
    <MaterialTaskBar task={task ?? null} onStop={() => task && void tasks.stop(workspaceId, task.turn_id)} onOpenActivity={() => void useWorkspaceStore.getState().openDrawer()} />
    {task && !running && task.unfinished.some(item => item.path === chapterPath) && <p className="border-b border-row-edge p-3 text-sm text-amber">本章整理尚未完成，已有记录仍保留。可重新整理本章，或查看活动记录。</p>}
    {owner && tasks.error && <StatusMessage tone="error">{tasks.error}</StatusMessage>}
    {error && <StatusMessage tone="error" action={<Button size="compact" onClick={() => void clues.loadList(workspaceId, chapterPath, asOf)}>重新读取</Button>}>{error}</StatusMessage>}
    <div className="min-h-0 flex-1 overflow-auto">
      {status && <p className={`border-b border-row-edge p-3 text-sm ${stale ? "text-amber" : "text-sub"}`}>{status}</p>}
      {coverage?.note && coverage.outcome === "insufficient" && <p className="p-3 text-sm text-sub">{coverage.note}</p>}
      {selected ? <ClueDetail workspaceId={workspaceId} clueId={selected.clue_id} asOf={data!.as_of_chapter} refreshKey={data?.view_revision}
        onClose={() => clues.select(key, null)} onLocate={id => onLocate(id, data!.as_of_chapter)} onReview={onReview} onReference={onReference} /> : <>
        <ul className="divide-y divide-row-edge">{data?.items.map(item => <li key={item.clue_id}>
          <button className="w-full px-3 py-3 text-left hover:bg-ui-selected" onClick={() => clues.select(key, item.clue_id)}>
            <span className="block break-words text-sm font-bold">{item.title}</span>
            <span className="mt-1 block text-xs text-sub">{clueStatus(item)}{item.lifecycle.planted_chapter !== null && ` · 第 ${item.lifecycle.planted_chapter} 章埋设`}{item.freshness !== "valid" && item.freshness !== "none" && " · 来源需复核"}</span>
          </button>
        </li>)}</ul>
        {data?.next_cursor && <Button className="m-3" size="compact" disabled={clues.loading[key]} onClick={() => void clues.loadList(workspaceId, chapterPath, asOf, true)}>更多线索</Button>}
        {data && data.items.length === 0 && coverage?.outcome === "found" && <p className="p-3 text-sm text-sub">当前范围没有可显示的线索，请重新读取或整理。</p>}
      </>}
    </div>
    {chapterPath && <footer className="flex flex-none flex-wrap gap-2 border-t border-panel-edge p-3">
      <Button size="compact" onClick={() => void organize()} disabled={occupied || !chapter?.digest || !chapter.has_content || Boolean(clues.loading[key])}>{coverage?.outcome === "unrecorded" ? "整理本章" : "重新整理本章"}</Button>
      {!running && <Button size="compact" variant="quiet" disabled={Boolean(clues.loading[key])} onClick={() => void clues.loadList(workspaceId, chapterPath, asOf)}>重新读取</Button>}
    </footer>}
  </section>;
}
