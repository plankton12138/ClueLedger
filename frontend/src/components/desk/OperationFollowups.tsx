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
import { fetchOperationImpact, type ImpactWire } from "../../api/impact";

export default function OperationFollowups({
  projectId,
  operationId,
}: {
  projectId: string;
  operationId: string;
}) {
  const [data, setData] = useState<Followups | null>(null);
  const [impact, setImpact] = useState<ImpactWire | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setImpact(null);
    setError(false);
    fetchOperation(projectId, operationId)
      .then((loaded) => {
        if (alive) setData(loaded);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    // 影响是补充信息：失败不影响后续状态本身。
    fetchOperationImpact(projectId, operationId)
      .then((loaded) => {
        if (alive) setImpact(loaded);
      })
      .catch(() => {
        if (alive) setImpact(null);
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
          {impact && (impact.chapters.length > 0 || impact.ledger.length > 0) && (
            <div className="border-t border-line pt-2">
              <p>
                <span className="text-sub">本次影响 · </span>
                {impact.chapters.length} 章 · {impact.ledger.length} 条账本
              </p>
              <ul className="mt-1 space-y-0.5 text-[10px] leading-4 text-sub">
                {impact.chapters.map((chapter) => (
                  <li key={chapter.chapter_id}>
                    {chapter.title || chapter.chapter_id}：+{chapter.inserted_chars} / -
                    {chapter.deleted_chars} 字
                    {chapter.has_later_changes ? "（之后又改过）" : ""}
                  </li>
                ))}
              </ul>
              {impact.related_acceptances.length > 0 && (
                <p className="mt-1 text-[10px] leading-4 text-sub">
                  关联采用 {impact.related_acceptances.length} 次（独立提交，可分别撤销）
                </p>
              )}
              <p className="mt-1 text-[10px] leading-4 text-sub">{impact.note}</p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
