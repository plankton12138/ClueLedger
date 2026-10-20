/**
 * 设定集面板（V03-10 M3）：只读，按 4 类分组展示正式设定。
 *
 * 「设定集」是前端显示名（旧「故事账本」）；增删改都在对话里由模型提议、
 * 经对话内「采用」落盘，这里只读。后端 `GET /ledger` 返回 10 类 kind，
 * 本面板只渲染 4 类（人物 / 线索 / 时间线 / 谜底），其余不显示。
 */

import { useEffect, useState } from "react";

import { fetchLedger, type LedgerEntry } from "../../api/ledger";
import { message } from "../../state/v2";

/** 只读展示的 4 类，顺序固定；`kind` 用后端 kind 值，标签用类内中文名。 */
const GROUPS: { kind: string; label: string }[] = [
  { kind: "story.character", label: "人物" },
  { kind: "story.clue", label: "线索" },
  { kind: "story.timeline_event", label: "时间线" },
  { kind: "story.mystery_truth", label: "谜底" },
];

export default function LedgerPanel({ projectId }: { projectId: string }) {
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchLedger(projectId)
      .then((rows) => {
        if (!alive) return;
        setEntries(rows);
        setError(null);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  const visibleCount = entries.filter((e) =>
    GROUPS.some((g) => g.kind === e.kind),
  ).length;

  return (
    <section className="panel" aria-label="设定集">
      <header className="panel-head px-4 py-3">
        <span>设定集</span>
      </header>
      {error && <p className="px-4 py-2 text-[11px] text-danger">{error}</p>}
      {entries.length === 0 ? (
        <p className="px-4 py-3 text-xs leading-6 text-sub">
          还没有正式设定。在对话里让 Agent 提议人物、线索或谜底，采用后才会出现在这里。
        </p>
      ) : visibleCount === 0 ? (
        <p className="px-4 py-3 text-xs leading-6 text-sub">
          这些设定不属于人物 / 线索 / 时间线 / 谜底，暂不在此展示。
        </p>
      ) : (
        GROUPS.map((group) => {
          const rows = entries.filter((entry) => entry.kind === group.kind);
          if (rows.length === 0) return null;
          return (
            <div key={group.kind}>
              <header className="panel-head px-4 py-2 text-xs">
                {group.label} · {rows.length}
              </header>
              <ul className="px-2 py-1 text-sm">
                {rows.map((entry) => (
                  <li key={entry.id} className="asset-row px-2 py-1.5">
                    <span className="text-fgStrong">{entry.title}</span>
                    <span className="block truncate text-[10px] text-sub">
                      {entry.summary}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
  );
}
