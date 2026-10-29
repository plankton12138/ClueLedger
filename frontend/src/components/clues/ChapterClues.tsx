import { useEffect } from "react";
import { clueListKey, useCluesStore } from "../../state/clues";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
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
  const fileDigest = useWorkspaceStore(state => state.file?.digest);
  const key = clueListKey(chapterPath, asOf);
  const data = clues.workspaceId === workspaceId ? clues.lists[key] : undefined;
  const error = clues.workspaceId === workspaceId ? clues.errors[key] : undefined;
  const selected = data?.items.find(item => item.clue_id === clues.selected[key]);
  useEffect(() => { if (active && chapterPath) void useCluesStore.getState().loadList(workspaceId, chapterPath, asOf); }, [workspaceId, chapterPath, asOf, active, fileDigest]);
  if (!active) return null;
  const chapter = data?.chapter;
  const coverage = chapter?.coverage;
  const stale = coverage && coverage.freshness !== "valid" && coverage.outcome !== "unrecorded";
  // 线索只来自写作与核对：这里只读，不再从面板发起整理任务。
  const status = !chapterPath ? "选择一章正文，查看与本章相关的线索。" : !data ? "正在读取本章线索…"
    : !chapter ? "当前查看范围尚未包含本章。"
    : !chapter.has_content ? "这一章还没有可读的正文。"
    : stale ? "正文已变化或暂不可读，已有线索记录需要重新核对。"
    : coverage?.outcome === "unrecorded" ? "本章还没有线索记录。写作或核对时会按正文登记。"
    : coverage?.outcome === "empty" ? "已核对，未发现相关线索。"
    : coverage?.outcome === "insufficient" ? "本章信息不足，尚不能形成完整线索记录。"
    : "";
  return <section className="flex h-full min-h-0 flex-col" aria-label="本章线索">
    <header className="flex flex-none flex-wrap items-center gap-2 border-b border-panel-edge p-3">
      <h2 className="min-w-0 flex-1 truncate font-bold" title={chapter?.title}>{chapter?.title ?? "本章线索"}</h2>
      {chapter && <p className="basis-full text-xs text-sub">查看截至第 {data?.as_of_chapter} 章</p>}
    </header>
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
        {data && data.items.length === 0 && coverage?.outcome === "found" && <p className="p-3 text-sm text-sub">当前范围没有可显示的线索，请重新读取。</p>}
      </>}
    </div>
    {chapterPath && <footer className="flex flex-none flex-wrap gap-2 border-t border-panel-edge p-3">
      <Button size="compact" variant="quiet" disabled={Boolean(clues.loading[key])} onClick={() => void clues.loadList(workspaceId, chapterPath, asOf)}>重新读取</Button>
    </footer>}
  </section>;
}
