/**
 * 故事账本面板（V03-3.2）：列出正式条目、直接新增/编辑，保存后显示真实版本。
 *
 * 只展示已确认的正式条目；候选与假设不混入这里（候选入口在 V03-3.2 的下一部分）。
 */

import { useCallback, useEffect, useState } from "react";

import { fetchLedger, type LedgerEntry, type LedgerSaveResult } from "../../api/ledger";
import { message } from "../../state/v2";
import LedgerEditor from "./LedgerEditor";

export default function LedgerPanel({
  projectId,
  onSaved,
}: {
  projectId: string;
  onSaved?(): void;
}) {
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ title: string; commitId: string | null; replayed: boolean } | null>(
    null,
  );
  const [editing, setEditing] = useState<{ entryId?: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      const rows = await fetchLedger(projectId);
      setEntries(rows);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  function savedResult(result: LedgerSaveResult) {
    setSaved({
      title: result.entry.title,
      commitId: result.commit_id,
      replayed: result.replayed || result.status === "no_change",
    });
    void reload();
    onSaved?.();
  }

  return (
    <>
      <section className="panel" aria-label="故事账本">
        <header className="panel-head flex items-center justify-between px-4 py-3">
          <span>故事账本</span>
          <button
            className="btn px-2 py-1 text-[11px]"
            aria-label="新增设定"
            onClick={() => setEditing({})}
          >
            新增
          </button>
        </header>
        {saved && (
          <p className="px-4 py-2 text-[11px] text-success" role="status">
            已保存「{saved.title}」
            {saved.commitId ? ` · 版本 ${saved.commitId.slice(0, 8)}` : " · 内容没有变化"}
            {saved.replayed ? "（重复提交，未重复入账）" : ""}
          </p>
        )}
        {error && <p className="px-4 py-2 text-[11px] text-danger">{error}</p>}
        {entries.length === 0 ? (
          <p className="px-4 py-3 text-xs leading-6 text-sub">
            这里会保存你确认过的故事设定。人物、地点、线索都可以直接新增；
            还没决定的假设先不要保存。
          </p>
        ) : (
          <ul className="max-h-80 overflow-y-auto px-2 py-2 text-xs">
            {entries.map((entry) => (
              <li key={entry.id}>
                <button
                  className="asset-row block w-full px-2 py-1.5 text-left"
                  onClick={() => setEditing({ entryId: entry.id })}
                >
                  <span className="text-fgStrong">{entry.title}</span>
                  <span className="text-[10px] text-sub"> · {entry.kind_label}</span>
                  <span className="block truncate text-[10px] text-sub">{entry.summary}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {editing && (
        <LedgerEditor
          projectId={projectId}
          entryId={editing.entryId}
          onClose={() => setEditing(null)}
          onSaved={savedResult}
        />
      )}
    </>
  );
}
