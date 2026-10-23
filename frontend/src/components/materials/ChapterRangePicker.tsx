/**
 * 章节范围选择器（0.7）。
 *
 * 「本次整理哪些章节」与「查看截至第几章」是两件事：这里只决定这次任务的目标，
 * 不改变页面正在看的历史时点。默认预选当前范围内尚未整理或已失效的章节，
 * 最多 10 章；选中清单始终完整显示。
 */

import { useEffect, useMemo, useState } from "react";

import type { MaterialChapter, MaterialEntry, MaterialEntity } from "../../api/storyMaterials";

interface Props {
  open: boolean;
  title: string;
  actionLabel: string;
  chapters: MaterialChapter[];
  entries: MaterialEntry[];
  entities?: MaterialEntity[];
  entitiesRequired?: boolean;
  maxChapters?: number;
  onCancel: () => void;
  onConfirm: (chapterPaths: string[], entityPaths: string[]) => void;
}

export default function ChapterRangePicker({
  open,
  title,
  actionLabel,
  chapters,
  entries,
  entities = [],
  entitiesRequired = false,
  maxChapters = 10,
  onCancel,
  onConfirm,
}: Props) {
  const defaults = useMemo(() => {
    const pending = entries
      .filter((entry) => entry.coverage === "unrecorded" || entry.freshness === "stale")
      .map((entry) => (entry.entity_path ? null : entry.chapter))
      .filter((value): value is number => value !== null);
    const numbers = pending.length ? pending : chapters.slice(0, 1).map((item) => item.chapter);
    return chapters
      .filter((item) => numbers.includes(item.chapter))
      .slice(0, maxChapters)
      .map((item) => item.path);
  }, [chapters, entries, maxChapters]);

  const [selected, setSelected] = useState<string[]>(defaults);
  const [selectedEntities, setSelectedEntities] = useState<string[]>(
    entitiesRequired && entities.length ? [entities[0].path] : [],
  );

  useEffect(() => {
    if (!open) return;
    setSelected(defaults);
    setSelectedEntities(entitiesRequired && entities.length ? [entities[0].path] : []);
  }, [open, defaults, entitiesRequired, entities]);

  if (!open) return null;

  const toggle = (path: string) => {
    setSelected((current) => {
      if (current.includes(path)) return current.filter((item) => item !== path);
      if (current.length >= maxChapters) return current;
      return [...current, path];
    });
  };

  const toggleEntity = (path: string) => {
    setSelectedEntities((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );
  };

  const ready =
    selected.length > 0 && (!entitiesRequired || selectedEntities.length > 0);

  return (
    <div
      className="fixed inset-0 z-30 flex items-start justify-center bg-black/25 pt-16"
      role="dialog"
      aria-label={title}
    >
      <div className="flex max-h-[76vh] w-[min(560px,92vw)] flex-col rounded border border-panel-edge bg-surface shadow-xl">
        <div className="flex flex-none items-center gap-2 border-b border-panel-edge px-3 py-2">
          <h2 className="min-w-0 flex-1 text-sm font-bold">{title}</h2>
          <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={onCancel}>
            取消
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-3 py-2 text-[11px]">
          {entitiesRequired && (
            <section className="mb-3">
              <h3 className="mb-1 font-bold text-sub">人物（至少一个）</h3>
              {entities.length === 0 ? (
                <p className="text-amber">
                  这个作品还没有角色小传。先在对话里建立小传，再回来整理人物资料。
                </p>
              ) : (
                <ul className="grid grid-cols-2 gap-1">
                  {entities.map((entity) => (
                    <li key={entity.path}>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={selectedEntities.includes(entity.path)}
                          onChange={() => toggleEntity(entity.path)}
                        />
                        <span className="truncate">{entity.name}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section>
            <h3 className="mb-1 font-bold text-sub">
              章节（已选 {selected.length}/{maxChapters}）
            </h3>
            {chapters.length === 0 ? (
              <p className="text-mute">这个作品还没有可整理的章节。</p>
            ) : (
              <ul className="space-y-0.5">
                {chapters.map((chapter) => {
                  const checked = selected.includes(chapter.path);
                  return (
                    <li key={chapter.path}>
                      <label className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-panel-head">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(chapter.path)}
                        />
                        <span className="min-w-0 flex-1 truncate">
                          第 {chapter.chapter} 章｜{chapter.title}
                        </span>
                        {!checked && selected.length >= maxChapters && (
                          <span className="text-[10px] text-mute">已满</span>
                        )}
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {selected.length > 0 && (
            <p className="mt-2 text-[10px] text-mute">
              本次整理：{selected.length} 章
              {entitiesRequired && ` · ${selectedEntities.length} 人`}
            </p>
          )}
        </div>

        <div className="flex flex-none items-center gap-2 border-t border-panel-edge px-3 py-2">
          <span className="min-w-0 flex-1 text-[10px] text-mute">
            一次最多 {maxChapters} 章；整理只发布派生资料，不改正文。
          </span>
          <button
            className="btn btn-gold px-3 py-1 text-[11px]"
            disabled={!ready}
            onClick={() => onConfirm(selected, selectedEntities)}
          >
            {actionLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
