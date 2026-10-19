/**
 * 图谱文字目录（V03-6.5）：不借助画布也能逐条查看图谱对象与关联。
 *
 * 设计依据（产品 §5.4/§5.8、验收 A22）：画布只是投影，键盘与目录必须具备相同能力。
 * 这里的列表是一个 `listbox`：容器持有焦点，方向键/Home/End 移动「当前行」，
 * 回车或空格选中——选中后与画布共用同一个详情区（查看条目、查找正文依据、加入讨论）。
 * 移动只是查看位置，不写任何领域数据；鼠标点选同样只改选中，不改布局偏好。
 */

import { useEffect, useMemo, useState } from "react";

import { evidenceLabel } from "../../state/graphLabels";
import {
  flattenRows,
  navigateRows,
  rowAtIndex,
  rowDomId,
  type DirectoryGroup,
  type DirectoryRow,
} from "../../state/graphDirectory";
import { Pill } from "../Workbench";

export default function GraphDirectory({
  groups,
  selectedKey,
  emptyText,
  onSelect,
}: {
  groups: DirectoryGroup[];
  /** 已选中对象的行键（与画布共用同一选中口径）。 */
  selectedKey: string | null;
  emptyText: string;
  onSelect(row: DirectoryRow): void;
}) {
  const rows = useMemo(() => flattenRows(groups), [groups]);
  const [activeKey, setActiveKey] = useState<string | null>(null);

  // 目录内容变化或选中对象变化时，把「当前行」落到仍然存在的行上。
  useEffect(() => {
    if (activeKey !== null && rows.some((row) => row.key === activeKey)) return;
    const fallback =
      selectedKey !== null && rows.some((row) => row.key === selectedKey)
        ? selectedKey
        : (rows[0]?.key ?? null);
    setActiveKey(fallback);
  }, [rows, selectedKey, activeKey]);

  // 键盘移动到新行时把它滚进可见区（焦点在容器上，靠 aria-activedescendant 定位）。
  useEffect(() => {
    if (activeKey === null) return;
    document.getElementById(rowDomId(activeKey))?.scrollIntoView({ block: "nearest" });
  }, [activeKey]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (rows.length === 0) return;
    if (event.key === "Enter" || event.key === " ") {
      const row = rowAtIndex(rows, activeKey);
      if (row) {
        event.preventDefault();
        onSelect(row);
      }
      return;
    }
    const next = navigateRows(rows, activeKey, event.key);
    if (next === null) return;
    event.preventDefault();
    setActiveKey(next);
  }

  if (rows.length === 0) {
    return <p className="px-3 py-3 text-[11px] leading-5 text-sub">{emptyText}</p>;
  }

  return (
    <div
      role="listbox"
      tabIndex={0}
      aria-label="图谱文字目录（方向键移动，回车查看）"
      aria-activedescendant={activeKey === null ? undefined : rowDomId(activeKey)}
      className="min-h-0 flex-1 overflow-y-auto px-2 py-2 outline-none ring-inset focus:ring-1 focus:ring-blue"
      onKeyDown={handleKeyDown}
    >
      {groups.map((group) => (
        <div key={group.key} className="mb-2" role="group" aria-label={group.label}>
          <p className="px-1 py-0.5 text-[10px] text-sub">{group.label}</p>
          {group.rows.map((row) => {
            const badge = evidenceLabel(row.evidenceStatus);
            const selected = row.key === selectedKey;
            const active = row.key === activeKey;
            return (
              <div
                key={row.key}
                id={rowDomId(row.key)}
                role="option"
                aria-selected={selected}
                title={row.detail || row.title}
                className={`flex items-start gap-2 rounded px-2 py-1 text-[11px] leading-5 ${
                  row.depth === 1 ? "ml-3" : ""
                } ${selected ? "bg-blue-50 text-blue-700" : active ? "bg-slate-100" : "hover:bg-slate-50"}`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setActiveKey(row.key);
                  onSelect(row);
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-fgStrong">{row.title}</span>
                  {row.detail && <span className="block truncate text-sub">{row.detail}</span>}
                  {row.note && <span className="block text-[10px] text-sub">{row.note}</span>}
                </span>
                <span className="flex shrink-0 items-center gap-1 pt-0.5">
                  <span className="text-[10px] text-sub">{row.badge}</span>
                  <Pill tone={badge.tone}>{badge.text}</Pill>
                </span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
