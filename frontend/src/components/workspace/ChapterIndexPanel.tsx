/**
 * 章节索引面板（0.6 V06-A3）。
 *
 * 列出每章的索引状态：有效、已过期、源文件已删除或还没生成。过期记录不会注入模型，
 * 作者可以重新选择章节发起一次索引任务——这由界面明确发起，不由模型从文字里猜。
 *
 * 面板只读：它不直接改作品，索引任务本身也只能读原文并保存索引缓存。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchChapterIndex, type ChapterIndexItem, type ChapterIndexStatus } from "../../api/workspace";
import { useConversationsStore } from "../../state/conversations";
import { useWorkspaceStore } from "../../state/workspace";

const STATUS_LABEL: Record<ChapterIndexStatus, string> = {
  missing: "未生成",
  valid: "有效",
  stale: "已过期",
  source_missing: "源文件已删除",
  unavailable: "无法读取",
};

const STATUS_TONE: Record<ChapterIndexStatus, string> = {
  missing: "text-mute",
  valid: "text-green",
  stale: "text-amber",
  source_missing: "text-amber",
  unavailable: "text-red",
};

const ENTRY_LABEL = {
  event: "事件",
  character: "人物",
  place: "时间地点",
  clue: "线索",
} as const;

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ChapterIndexPanel({ open, onClose }: Props) {
  const workspaceId = useWorkspaceStore((state) => state.currentId);
  const catalog = useWorkspaceStore((state) => state.catalog);
  const send = useConversationsStore((state) => state.send);
  const pending = useConversationsStore((state) => state.pendingKey);
  const [items, setItems] = useState<ChapterIndexItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const index = await fetchChapterIndex(workspaceId);
      setItems(index.items);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "索引读取失败");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const byPath = useMemo(() => new Map(items.map((item) => [item.source_path, item])), [items]);
  const rows = useMemo(
    () =>
      catalog.chapters.map((chapter) => ({
        path: chapter.path,
        title: chapter.display_title || chapter.title || chapter.path,
        item: byPath.get(chapter.path) ?? null,
        status: (byPath.get(chapter.path)?.status ?? "missing") as ChapterIndexStatus,
      })),
    [catalog.chapters, byPath],
  );

  if (!open) return null;

  const toggle = (path: string) => {
    setSelected((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );
  };

  const submit = async () => {
    if (selected.length === 0) return;
    const titles = rows.filter((row) => selected.includes(row.path)).map((row) => row.title);
    await send(
      `更新章节索引：${titles.join("、")}`,
      undefined,
      { purpose: "update_chapter_index", indexPaths: selected },
    );
    setSelected([]);
    await load();
  };

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/30" role="dialog" aria-label="章节索引">
      <aside className="flex h-full w-[min(520px,92vw)] flex-col bg-surface shadow-xl">
        <div className="flex flex-none items-center gap-2 border-b border-panel-edge px-3 py-2">
          <h2 className="min-w-0 flex-1 text-sm font-bold">章节索引</h2>
          <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={() => void load()}>
            刷新
          </button>
          <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-3 py-2 text-[11px]">
          {loading && <p className="text-mute">正在读取索引…</p>}
          {error && <p className="text-red">{error}</p>}
          {!loading && rows.length === 0 && <p className="text-mute">这个作品还没有章节。</p>}
          <ul className="flex flex-col gap-2">
            {rows.map((row) => (
              <li key={row.path} className="rounded border border-panel-edge p-2">
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={selected.includes(row.path)}
                    onChange={() => toggle(row.path)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-bold">{row.title}</span>
                      <span className={`text-[10px] ${STATUS_TONE[row.status]}`}>
                        {STATUS_LABEL[row.status]}
                      </span>
                    </span>
                    {row.item && row.item.status !== "missing" && (
                      <span className="mt-1 block text-[11px] leading-5 text-sub">
                        {row.item.summary}
                      </span>
                    )}
                    {row.item && row.item.status === "stale" && (
                      <span className="mt-1 block text-[10px] text-amber">
                        源文件在生成后改动过；这份摘要不再注入模型，需要重新生成。
                      </span>
                    )}
                    {row.item && (
                      <details className="mt-1 text-[10px] text-mute">
                        <summary className="cursor-pointer">
                          证据 {row.item.evidence.length} 处 · 条目{" "}
                          {row.item.events.length +
                            row.item.characters.length +
                            row.item.places.length +
                            row.item.clues.length}{" "}
                          条
                        </summary>
                        <ul className="mt-1 flex flex-col gap-0.5">
                          {(
                            [
                              ["event", row.item.events],
                              ["character", row.item.characters],
                              ["place", row.item.places],
                              ["clue", row.item.clues],
                            ] as const
                          ).map(([kind, entries]) =>
                            entries.map((entry, index) => (
                              <li key={`${kind}-${index}`}>
                                {ENTRY_LABEL[kind]}：{entry.text}（证据{" "}
                                {entry.evidence.join("、")}）
                              </li>
                            )),
                          )}
                        </ul>
                        <ul className="mt-1 flex flex-col gap-0.5">
                          {row.item.evidence.map((evidence) => (
                            <li key={evidence.index} className="truncate">
                              证据 {evidence.index}：{evidence.quote}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-none items-center gap-2 border-t border-panel-edge px-3 py-2">
          <span className="min-w-0 flex-1 text-[10px] text-mute">
            每轮最多 10 章；索引只读原文生成，不改作品。
          </span>
          <button
            className="btn btn-gold px-3 py-1 text-[11px]"
            disabled={selected.length === 0 || Boolean(pending)}
            onClick={() => void submit()}
          >
            更新所选索引（{selected.length}）
          </button>
        </div>
      </aside>
    </div>
  );
}
