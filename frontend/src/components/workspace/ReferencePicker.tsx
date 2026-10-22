/**
 * 参考选择（0.6 V06-A2）。
 *
 * 输入区的「添加文件」：从当前作品的章节、人物小传与固定设定里多选。选择只说明
 * 「这一轮可以参考什么」，不产生任何修改授权，也不会替作者打开或读取文件。
 *
 * 没有选择时不显示标签区；发送不要求先选文件。
 */

import { useMemo, useState } from "react";

import type { Catalog, FileEntry } from "../../api/workspace";

interface Props {
  catalog: Catalog;
  /** 已选路径，顺序即选择顺序。 */
  selected: string[];
  onToggle: (path: string) => void;
  onClear: () => void;
}

function entryLabel(entry: FileEntry): string {
  return entry.display_title || entry.title || entry.path;
}

export default function ReferencePicker({ catalog, selected, onToggle, onClear }: Props) {
  const [open, setOpen] = useState(false);
  const groups = useMemo(
    () =>
      [
        { label: "正文", entries: catalog.chapters },
        { label: "角色小传", entries: catalog.characters },
        { label: "固定设定", entries: catalog.settings },
      ].filter((group) => group.entries.length > 0),
    [catalog],
  );
  const titles = useMemo(() => {
    const map = new Map<string, string>();
    for (const entry of [...catalog.chapters, ...catalog.characters, ...catalog.settings]) {
      map.set(entry.path, entryLabel(entry));
    }
    return map;
  }, [catalog]);

  return (
    <div className="flex flex-col gap-1">
      {selected.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" aria-label="本轮参考">
          <span className="text-[10px] text-mute">本轮参考</span>
          {selected.map((path) => (
            <span
              key={path}
              className="flex items-center gap-1 rounded border border-panel-edge px-1.5 py-0.5 text-[10px] text-sub"
            >
              {titles.get(path) ?? path}
              <button
                type="button"
                aria-label={`移除参考 ${path}`}
                className="text-mute hover:text-amber"
                onClick={() => onToggle(path)}
              >
                ×
              </button>
            </span>
          ))}
          <button type="button" className="text-[10px] text-mute underline" onClick={onClear}>
            清空
          </button>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-gray px-2 py-0.5 text-[10px]"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          + 添加文件
        </button>
        <span className="text-[10px] text-mute">
          {selected.length > 0 ? `已选 ${selected.length} 份` : "可选：让这一轮参考其他文件"}
        </span>
      </div>

      {open && (
        <div className="max-h-56 overflow-auto rounded border border-panel-edge bg-panel p-2">
          {groups.length === 0 && <p className="text-[11px] text-mute">这个作品还没有可参考的文件。</p>}
          {groups.map((group) => (
            <div key={group.label} className="mb-2 last:mb-0">
              <p className="mb-1 text-[10px] text-mute">{group.label}</p>
              {group.entries.map((entry) => (
                <label
                  key={entry.path}
                  className="flex items-center gap-2 px-1 py-0.5 text-[11px] text-sub"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(entry.path)}
                    disabled={!entry.exists}
                    onChange={() => onToggle(entry.path)}
                  />
                  <span className="min-w-0 flex-1 truncate">{entryLabel(entry)}</span>
                  {!entry.exists && <span className="text-[10px] text-amber">缺失</span>}
                </label>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
