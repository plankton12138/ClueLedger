/**
 * 故事账本面板（V03-3.2）：正式条目、直接新增/编辑、待确认候选成组。
 *
 * 正式条目与候选分开呈现：候选只进辅助表，选中确认后才一次入账；
 * 假设可以暂存但不会进入正式账本。保存/确认后都显示真实版本。
 */

import { useCallback, useEffect, useState } from "react";

import {
  acceptLedgerCandidates,
  fetchLedger,
  fetchLedgerCandidates,
  skipLedgerCandidates,
  type LedgerCandidateGroup,
  type LedgerEntry,
  type LedgerSaveResult,
} from "../../api/ledger";
import { V2Error } from "../../api/v2";
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
  const [group, setGroup] = useState<LedgerCandidateGroup | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<{
    title: string;
    commitId: string | null;
    replayed: boolean;
  } | null>(null);
  const [editing, setEditing] = useState<{ entryId?: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      const [rows, candidates] = await Promise.all([
        fetchLedger(projectId),
        fetchLedgerCandidates(projectId),
      ]);
      setEntries(rows);
      setGroup(candidates);
      setSelected((current) =>
        current.filter((id) =>
          candidates.items.some((item) => item.item_id === id && item.status === "pending"),
        ),
      );
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

  function staged(next: LedgerCandidateGroup) {
    setGroup(next);
    setSaved(null);
    setSelected([]);
    setError(null);
  }

  async function acceptSelected() {
    if (!group || selected.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await acceptLedgerCandidates(projectId, {
        item_ids: selected,
        expected_revision: group.revision,
        request_id: crypto.randomUUID(),
      });
      setSaved({
        title: `${result.entries.length} 条设定`,
        commitId: result.commit_id,
        replayed: false,
      });
      setGroup(result.group);
      setSelected([]);
      const rows = await fetchLedger(projectId);
      setEntries(rows);
      onSaved?.();
    } catch (e) {
      if (e instanceof V2Error && e.code === "LEDGER_CANDIDATE_CONFLICT") {
        setError("待确认列表已更新，本次选择基于旧列表；已重新读取，请再确认一次。");
        await reload();
      } else {
        setError(message(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function skipSelected() {
    if (!group || selected.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const next = await skipLedgerCandidates(projectId, {
        item_ids: selected,
        expected_revision: group.revision,
        request_id: crypto.randomUUID(),
      });
      setGroup(next);
      setSelected([]);
    } catch (e) {
      if (e instanceof V2Error && e.code === "LEDGER_CANDIDATE_CONFLICT") {
        setError("待确认列表已更新；已重新读取，请再选择一次。");
        await reload();
      } else {
        setError(message(e));
      }
    } finally {
      setBusy(false);
    }
  }

  const pending = group?.items.filter((item) => item.status === "pending") ?? [];

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
          <ul className="max-h-72 overflow-y-auto px-2 py-2 text-sm">
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

      {pending.length > 0 && (
        <section className="panel" aria-label="待确认设定">
          <header className="panel-head px-4 py-3">
            待确认 · {pending.length}
            {(group?.hypothesis_count ?? 0) > 0 && (
              <span className="ml-2 text-[10px] text-sub">
                其中 {group?.hypothesis_count} 条是假设
              </span>
            )}
          </header>
          <ul className="max-h-64 overflow-y-auto px-2 py-2 text-sm">
            {pending.map((item) => (
              <li key={item.item_id} className="flex items-start gap-2 px-2 py-1.5">
                <input
                  type="checkbox"
                  className="mt-1"
                  aria-label={`选择候选 ${item.title}`}
                  disabled={item.hypothesis || busy}
                  checked={selected.includes(item.item_id)}
                  onChange={(e) =>
                    setSelected((current) =>
                      e.target.checked
                        ? [...current, item.item_id]
                        : current.filter((id) => id !== item.item_id),
                    )
                  }
                />
                <span className="min-w-0">
                  <span className="text-fgStrong">{item.title}</span>
                  <span className="text-[10px] text-sub"> · {item.kind_label}</span>
                  {item.hypothesis && (
                    <span className="ml-1 text-[10px] text-danger">假设 · 不会入账</span>
                  )}
                  <span className="block truncate text-[10px] text-sub">{item.summary}</span>
                </span>
              </li>
            ))}
          </ul>
          <div className="flex gap-2 px-4 py-2">
            <button
              className="btn btn-gray px-3 py-1.5 text-[11px]"
              disabled={busy || selected.length === 0}
              onClick={() => void skipSelected()}
            >
              暂不处理
            </button>
            <button
              className="btn px-3 py-1.5 text-[11px]"
              disabled={busy || selected.length === 0}
              onClick={() => void acceptSelected()}
            >
              {busy ? "处理中…" : `确认选中（${selected.length}）`}
            </button>
          </div>
        </section>
      )}

      {editing && (
        <LedgerEditor
          projectId={projectId}
          entryId={editing.entryId}
          onClose={() => setEditing(null)}
          onSaved={savedResult}
          onStaged={staged}
        />
      )}
    </>
  );
}
