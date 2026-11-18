import { useCallback, useEffect, useState } from "react";

import { type QaQueue, fetchQaQueue } from "./api";

export function QaPage({
  onOpenItem,
}: {
  onOpenItem: (runId: string, caseId: string, attempt: number) => void;
}) {
  const [queue, setQueue] = useState<QaQueue | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    try {
      setQueue(await fetchQaQueue());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const summary = queue?.summary || { pending: 0, reviewed: 0, eval_bug: 0 };
  const items = queue?.items || [];

  return (
    <div className="wb-page">
      <div className="wb-page-head">
        <div>
          <h2>质检</h2>
          <p className="wb-muted">跨多次运行的待人工复核；点行打开对应试验</p>
        </div>
        <button type="button" className="wb-chip ghost" onClick={() => void reload()}>
          刷新
        </button>
      </div>
      {error && (
        <p className="wb-error" role="alert">
          {error}
        </p>
      )}
      <div className="wb-kpi-grid" style={{ maxWidth: 720 }}>
        <div className="wb-kpi">
          <div className="label">待复核</div>
          <strong>{summary.pending}</strong>
        </div>
        <div className="wb-kpi">
          <div className="label">已复核</div>
          <strong>{summary.reviewed}</strong>
        </div>
        <div className="wb-kpi">
          <div className="label">评测判错</div>
          <strong>{summary.eval_bug}</strong>
        </div>
      </div>
      <div className="wb-table-wrap">
        <table className="wb-table">
          <thead>
            <tr>
              <th>案例 / run</th>
              <th>检查项</th>
              <th>机器</th>
              <th>人工</th>
              <th>归因</th>
            </tr>
          </thead>
          <tbody>
            {!items.length && (
              <tr>
                <td colSpan={5} className="wb-muted">
                  队列为空
                </td>
              </tr>
            )}
            {items.map(item => (
              <tr
                key={`${item.run_id}:${item.case_id}:${item.attempt}:${item.check_id}`}
                className="click"
                style={{ cursor: "pointer" }}
                onClick={() => onOpenItem(item.run_id, item.case_id, item.attempt)}
              >
                <td>
                  <code>{item.case_id}</code> #{item.attempt}
                  <div className="wb-muted">{item.run_id}</div>
                </td>
                <td>
                  <code>{item.check_id}</code>
                  <div className="wb-muted">{item.level}</div>
                </td>
                <td>{item.machine_verdict}</td>
                <td>{item.human_verdict || "待定"}</td>
                <td>{item.attribution_category || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
