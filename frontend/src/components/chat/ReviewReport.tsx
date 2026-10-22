/**
 * 检查报告（0.6 V06-C1）。
 *
 * 只展示报告本身：问题、确定程度、证据与建议。没有发现问题时说明覆盖范围；
 * 未完成时说明哪些部分没覆盖——不把「没检查完」显示成「没问题」。
 *
 * 报告回看时按当前磁盘核对版本：目标已经改动或多了一份缺失，都要在卡片上说明，
 * 不能让旧结论继续当成现在的结论。
 */

import { useEffect, useState } from "react";

import { fetchReviews, type ReviewFinding, type ReviewStatus } from "../../api/workspace";
import type { TurnReview } from "../../state/chatMessages";
import { useConversationsStore } from "../../state/conversations";

type Freshness = "valid" | "stale" | "source_missing";

const FRESHNESS_NOTE: Record<Exclude<Freshness, "valid">, string> = {
  stale: "报告已过期：检查之后目标文件又改过，下面的结论不再对应当前版本。",
  source_missing: "报告对应的文件已经不在，下面的结论无法核对。",
};

const KIND_LABEL: Record<ReviewFinding["kind"], string> = {
  continuity: "前后矛盾",
  disclosure: "提前透露",
  task_mismatch: "与要求不符",
  other: "其他",
};

const STATUS_LABEL: Record<ReviewStatus, string> = {
  findings: "发现问题",
  no_findings: "没有发现问题",
  incomplete: "检查未完成",
};

interface Props {
  review: TurnReview;
}

export default function ReviewReportView({ review }: Props) {
  const workspaceId = useConversationsStore((state) => state.workspaceId);
  const [freshness, setFreshness] = useState<Freshness | null>(null);
  const reportId = review.reportId;

  useEffect(() => {
    if (!workspaceId || !reportId) return;
    let alive = true;
    void fetchReviews(workspaceId)
      .then((list) => {
        if (!alive) return;
        const found = list.items.find((item) => item.report_id === reportId);
        // 找不到（例如已超出保留条数）时不猜：没有核对结果就照原样展示。
        if (found) setFreshness(found.freshness);
      })
      .catch(() => {
        // 查询失败只影响过期提示，不改变这一轮已经形成的结论。
      });
    return () => {
      alive = false;
    };
  }, [workspaceId, reportId]);

  // 核到过期或缺失时给一句说明：旧结论不能继续当成现在的结果。
  const outdatedNote =
    freshness !== null && freshness !== "valid" ? FRESHNESS_NOTE[freshness] : null;
  const outdated = outdatedNote !== null;
  const tone = outdated
    ? "text-amber"
    : review.status === "no_findings"
      ? "text-green"
      : review.status === "incomplete"
        ? "text-amber"
        : "text-red";

  return (
    <div className="chat-card mt-1 text-[11px]">
      <div className="flex items-center gap-2">
        <span className={`font-bold ${tone}`}>{STATUS_LABEL[review.status]}</span>
        {review.findings.length > 0 && (
          <span className="text-sub">{review.findings.length} 处</span>
        )}
        {outdated && <span className="text-amber">已过期</span>}
      </div>

      {outdatedNote && <p className="mt-1 text-amber">{outdatedNote}</p>}

      {review.status === "no_findings" && !outdated && (
        <p className="mt-1 text-sub">
          已读完检查目标与作者指定的参考，没有发现需要处理的问题。
        </p>
      )}

      {review.status === "incomplete" && (
        <ul className="mt-1 space-y-0.5 text-amber">
          {review.uncovered.length > 0 ? (
            review.uncovered.map((item) => <li key={item}>· {item}</li>)
          ) : (
            <li>· 这一轮没有形成可用的检查结论。</li>
          )}
        </ul>
      )}

      {review.findings.length > 0 && (
        <ul className="mt-1 space-y-2">
          {review.findings.map((finding, index) => (
            <li key={`${finding.kind}-${index}`} className="border-t border-panel-edge pt-1">
              <div className="flex flex-wrap items-center gap-1">
                <span className="font-bold">{KIND_LABEL[finding.kind]}</span>
                {finding.certainty === "suspected" && (
                  <span className="text-[10px] text-amber">疑点</span>
                )}
              </div>
              <p className="mt-0.5 text-sub">{finding.description}</p>
              <p className="mt-0.5 text-mute">建议：{finding.suggestion}</p>
              <ul className="mt-0.5 space-y-0.5 text-[10px] text-mute">
                {finding.evidence.map((evidence, position) => (
                  <li key={`${evidence.path}-${position}`} className="truncate">
                    {evidence.path}：{evidence.quote}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
