/**
 * 操作结果之后的两个独立后续状态（V03-2.6 / I06）。
 *
 * 只显示状态，不提供「一键生成」：摘要与账本建议是两项独立的模型功能，
 * 启动入口随各自任务（V03-4）提供。作者没点过任何东西，这里就不会有 Job
 * 或模型调用——这是「不运行默认后台任务」在产品面的第一站。
 */

import { useEffect, useState } from "react";

import {
  fetchOperation,
  suggestionsLabel,
  summaryLabel,
  type OperationFollowups as Followups,
} from "../../api/followups";

export default function OperationFollowups({
  projectId,
  operationId,
}: {
  projectId: string;
  operationId: string;
}) {
  const [data, setData] = useState<Followups | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(false);
    fetchOperation(projectId, operationId)
      .then((loaded) => {
        if (alive) setData(loaded);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [projectId, operationId]);

  if (error) return null;
  return (
    <section className="panel" aria-label="后续状态">
      <header className="panel-head px-4 py-3">后续状态</header>
      {data === null ? (
        <p className="px-4 py-3 text-[11px] text-sub">正在读取…</p>
      ) : (
        <div className="space-y-2 px-4 py-3 text-xs leading-5">
          <p>
            <span className="text-sub">摘要 · </span>
            {summaryLabel(data.summary_state)}
          </p>
          <p>
            <span className="text-sub">账本建议 · </span>
            {suggestionsLabel(data.ledger_suggestions_state)}
          </p>
          <p className="text-[10px] leading-4 text-sub">
            两项都是独立的模型功能，启动入口随各自任务提供；在此之前不会自动运行。
          </p>
        </div>
      )}
    </section>
  );
}
