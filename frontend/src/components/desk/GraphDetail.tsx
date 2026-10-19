/**
 * 图谱选中对象详情（V03-6.4）：点节点看条目、点连线看关系含义及来源。
 *
 * 设计依据（产品 §5.8）：从来源可定位正文（按被锚定对象查证据，跳转到来源章）；
 * 「加入讨论」把选中对象作为可见引用附到主输入框。两者都不写领域数据——定位只是
 * 读证据 + 导航，加入讨论只是把文字放进草稿。
 */

import { useEffect, useState } from "react";

import {
  fetchEvidenceByTarget,
  type ClueStateWire,
  type GraphEdgeWire,
  type GraphNodeWire,
} from "../../api/graphs";
import type { EvidenceLinkWire } from "../../api/history";
import { message } from "../../state/v2";
import { buildDiscussionReference } from "../../state/graphLayout";
import { clueStatusLabel, evidenceLabel, layerLabel, relationSummary } from "../../state/graphLabels";
import { ErrorText, Pill } from "../Workbench";

export default function GraphDetail({
  projectId,
  node,
  edge,
  nodesById,
  clue,
  chapterTitles,
  onAddToDiscussion,
  onNavigateChapter,
}: {
  projectId: string;
  node: GraphNodeWire | null;
  edge: GraphEdgeWire | null;
  nodesById: Record<string, GraphNodeWire>;
  clue: ClueStateWire | null;
  chapterTitles: Record<string, string>;
  onAddToDiscussion(reference: string): void;
  onNavigateChapter(chapterId: string): void;
}) {
  const [evidence, setEvidence] = useState<EvidenceLinkWire[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetId = node?.id ?? edge?.declared_by ?? edge?.source ?? null;
  // 换选中对象时清空上一次的来源定位结果，避免张冠李戴。
  useEffect(() => {
    setEvidence(null);
    setError(null);
  }, [targetId]);

  if (!node && !edge) {
    return (
      <p className="px-3 py-2 text-[11px] leading-5 text-sub">
        点节点查看条目与来源，点连线查看关系含义；从来源可以定位正文。
      </p>
    );
  }

  const title = node ? node.title || node.id : edge ? edge.label || edge.relation_type : "";
  const kindLabel = node ? node.kind_label || node.kind : "关系";
  const detailText = node ? node.summary : "";
  const revision = node ? node.revision : edge ? null : null;
  const sourceRefs = (node ? node.source_refs : edge?.source_refs) ?? [];

  async function locate() {
    if (targetId === null) return;
    setBusy(true);
    setError(null);
    try {
      setEvidence(await fetchEvidenceByTarget(projectId, targetId));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  function addToDiscussion() {
    onAddToDiscussion(
      buildDiscussionReference({ kindLabel, title, id: targetId ?? "", revision, detail: detailText }),
    );
  }

  return (
    <section className="border-t border-line px-3 py-2 text-xs" aria-label="图谱对象详情">
      <header className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-fgStrong">{title}</span>
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-sub">
          {kindLabel}
          {node && <span>· {layerLabel(node.semantic_layer)}</span>}
        </span>
      </header>
      {detailText && <p className="mt-1 leading-5">{detailText}</p>}

      {clue && (
        <p className="mt-1 leading-5 text-sub">
          线索状态：{clueStatusLabel(clue.status)}
          {clue.necessity ? " · 必要线索" : ""}
        </p>
      )}

      {edge && <p className="mt-1 leading-5 text-sub">{relationSummary(edge, nodesById)}</p>}

      <div className="mt-1 flex flex-wrap items-center gap-1">
        {(() => {
          const status = node ? node.evidence_status : edge?.evidence_status ?? "not_recorded";
          const badge = evidenceLabel(status);
          return <Pill tone={badge.tone}>{badge.text}</Pill>;
        })()}
        {revision && <span className="text-[10px] text-sub">版本 {revision.slice(0, 8)}</span>}
      </div>

      {sourceRefs.length > 0 && (
        <p className="mt-1 text-[10px] text-sub">
          来源：
          {sourceRefs.map((ref) => `${ref.kind}#${ref.id.slice(0, 8)}`).join("、")}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={busy || targetId === null}
          onClick={() => void locate()}
        >
          {busy ? "查找中…" : "查找正文依据"}
        </button>
        <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={addToDiscussion}>
          加入讨论
        </button>
      </div>

      {evidence !== null && (
        <div className="mt-2" aria-label="来源定位结果">
          {evidence.length === 0 ? (
            <p className="text-[11px] leading-5 text-sub">
              这条对象还没有正文证据。可以「加入讨论」请模型补写依据，或先在账本登记。
            </p>
          ) : (
            <ul className="space-y-1">
              {evidence.map((item) => (
                <li key={item.evidence_id} className="leading-5">
                  <button
                    className="text-left text-blue underline"
                    onClick={() => onNavigateChapter(item.chapter_id)}
                  >
                    {chapterTitles[item.chapter_id] ?? item.chapter_id}
                  </button>
                  <span className="block text-[10px] text-sub">
                    「{item.quote}」{item.status === "needs_check" ? " · 需核对" : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <ErrorText>{error}</ErrorText>
    </section>
  );
}
