/**
 * 页边索引（1.0.9 P4；P6 改为正文投影）。
 *
 * 只显示当前正文能证明的出处：已写入的信息线带准确原句；语义分析给出的「正文新增」
 * 只是候选，作者确认后才成为正式信息线；正文与已入账信息矛盾时进入「需要对齐」。
 * 点击原句在编辑器里选中同一段文字。
 */

import { FileText, LocateFixed } from "lucide-react";

import type {
  ChapterCluesView,
  ChapterInformationIndex,
  InformationOccurrence,
  InformationIndexItem,
} from "../../api/writing";
import { ChapterClues } from "../clues/CluePanel";
import { Button } from "../ui/Button";

const OCCURRENCE_LABEL: Record<string, string> = {
  exact: "",
  relocated: "位置已移动",
  missing: "原句不在正文",
  ambiguous: "原句出现多次",
  unavailable: "无法核对",
};

const ANALYSIS_LABEL: Record<string, string> = {
  queued: "正在整理正文中的信息",
  running: "正在整理正文中的信息",
  ready: "语义整理已就绪",
  failed: "语义整理未完成，只显示确定性出处",
  stale: "语义整理基于旧正文",
  unavailable: "尚未整理语义出处",
};

export default function ChapterMarginIndex({
  index,
  clues = null,
  activeItemId,
  onLocate,
  onConfirm,
  confirmingId,
  variant = "margin",
}: {
  index: ChapterInformationIndex | null;
  /** 本章线索登记（v1.3 P4）；与信息索引同panel展示，只读。 */
  clues?: ChapterCluesView | null;
  activeItemId?: string | null;
  onLocate: (item: InformationIndexItem, occurrence: InformationOccurrence) => void;
  onConfirm?: (item: InformationIndexItem) => void;
  confirmingId?: string | null;
  variant?: "margin" | "panel";
}) {
  const written = (index?.items ?? []).filter(item => item.placement === "written");
  const candidates = (index?.items ?? []).filter(item => item.placement === "observed_candidate");
  const alignment = (index?.items ?? []).filter(item => item.placement === "alignment");
  const analysisNote = index ? ANALYSIS_LABEL[index.analysis.state] : null;
  return <aside className={`chapter-margin ${variant === "panel" ? "chapter-margin-panel" : ""}`} aria-label="本章信息">
    <header className="chapter-margin-header">
      <h2>本章信息</h2>
      {analysisNote && <p>{analysisNote}</p>}
    </header>
    {clues && <ChapterClues clues={clues} />}
    {written.length > 0 && <section>
      <h3>已经写入</h3>
      <ul>
        {written.map(item => <li key={item.item_id} className={activeItemId === item.item_id ? "active" : ""}>
          <span className="chapter-margin-label">{item.label}</span>
          <div className="chapter-margin-occurrences">
            {item.occurrences.map((occurrence, position) => <button
              key={`${occurrence.path}:${occurrence.start ?? position}`}
              className="chapter-margin-occurrence"
              data-state={occurrence.state}
              onClick={() => onLocate(item, occurrence)}
              disabled={occurrence.start === null}
            >
              <LocateFixed size={12} strokeWidth={1.9} aria-hidden="true" />
              <span>{occurrence.quote.length > 26 ? `${occurrence.quote.slice(0, 26)}…` : occurrence.quote}</span>
              {OCCURRENCE_LABEL[occurrence.state] && <small>{OCCURRENCE_LABEL[occurrence.state]}</small>}
            </button>)}
          </div>
        </li>)}
      </ul>
    </section>}
    {candidates.length > 0 && <section>
      <h3>正文新增</h3>
      <ul>
        {candidates.map(item => <li key={item.item_id} className={activeItemId === item.item_id ? "active" : ""}>
          <span className="chapter-margin-label">{item.label}</span>
          {item.explanation && <p>{item.explanation}</p>}
          {item.occurrences[0]?.quote && <p className="chapter-margin-quote">“{item.occurrences[0].quote}”</p>}
          {onConfirm && item.candidate_id && <Button
            size="compact"
            variant="primary"
            busy={confirmingId === item.item_id}
            disabled={item.occurrences[0]?.state !== "exact"}
            disabledReason="原句不唯一或不在正文中，不能确认"
            onClick={() => onConfirm(item)}
          >确认入账</Button>}
        </li>)}
      </ul>
    </section>}
    {alignment.length > 0 && <section>
      <h3>需要对齐</h3>
      <ul>
        {alignment.map(item => <li key={item.item_id} className={activeItemId === item.item_id ? "active" : ""}>
          <span className="chapter-margin-label">{item.label}</span>
          {item.explanation && <p>{item.explanation}</p>}
        </li>)}
      </ul>
    </section>}
    {index && index.items.length === 0 && <p className="chapter-margin-empty">
      <FileText size={14} strokeWidth={1.8} aria-hidden="true" />这一章还没有可核对的信息变化。
    </p>}
  </aside>;
}
