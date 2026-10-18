/**
 * 正文证据核对（V03-2.5）：只列当前章需要人工核对的依据。
 *
 * 没有证据或全部有效时不占卡片（空待办不占空间）。重新关联要求作者给出
 * 当前正文中仍然存在的片段——服务端会再核对一次，找不到就拒绝。
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchEvidenceLinks,
  reanchorEvidence,
  type EvidenceLinkWire,
} from "../../api/history";
import { message } from "../../state/v2";
import { ErrorText } from "../Workbench";

const ANCHOR_LABEL: Record<string, string> = {
  setting: "设定",
  disclosure: "读者披露",
  cognition: "角色认知",
};

export default function EvidenceCheck({
  projectId,
  chapterId,
}: {
  projectId: string;
  chapterId: string;
}) {
  const [items, setItems] = useState<EvidenceLinkWire[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [quote, setQuote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await fetchEvidenceLinks(projectId, chapterId));
    } catch {
      setItems([]);
    }
  }, [projectId, chapterId]);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = items.filter((item) => item.status === "needs_check");
  if (items.length === 0) return null;

  async function submit(evidenceId: string) {
    setBusy(true);
    setError(null);
    try {
      await reanchorEvidence(projectId, evidenceId, quote.trim());
      setEditing(null);
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label="正文证据">
      <header className="panel-head px-4 py-3">
        正文证据
        <small>
          {pending.length > 0 ? `${pending.length} 条需核对` : `${items.length} 条有效`}
        </small>
      </header>
      {pending.length === 0 ? (
        <p className="px-4 py-3 text-xs leading-6 text-sub">
          当前章的正文依据都能在原文中定位。
        </p>
      ) : (
        pending.slice(0, 5).map((item) => (
          <div key={item.evidence_id} className="asset-row px-4 py-3 text-xs">
            <p className="text-[10px] text-sub">
              {ANCHOR_LABEL[item.anchor_kind] ?? item.anchor_kind} · {item.reason}
            </p>
            <p className="mt-1 line-clamp-2 leading-5">「{item.quote}」</p>
            {editing === item.evidence_id ? (
              <div className="mt-2 space-y-1">
                <input
                  className="well-input w-full p-1.5 text-[11px]"
                  aria-label="新的引用片段"
                  value={quote}
                  onChange={(e) => setQuote(e.target.value)}
                  placeholder="从当前正文复制一段原文"
                />
                <div className="flex gap-2">
                  <button
                    className="btn px-2 py-1 text-[11px]"
                    disabled={busy || quote.trim() === ""}
                    onClick={() => void submit(item.evidence_id)}
                  >
                    确认关联
                  </button>
                  <button
                    className="btn btn-gray px-2 py-1 text-[11px]"
                    onClick={() => setEditing(null)}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="mt-1 text-[11px] text-blue underline"
                onClick={() => {
                  setEditing(item.evidence_id);
                  setQuote(item.quote);
                  setError(null);
                }}
              >
                重新关联
              </button>
            )}
          </div>
        ))
      )}
      {pending.length > 5 && (
        <p className="px-4 py-2 text-[10px] text-sub">
          还有 {pending.length - 5} 条，在账本详情中处理。
        </p>
      )}
      <ErrorText>{error}</ErrorText>
    </section>
  );
}
