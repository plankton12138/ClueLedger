/**
 * 页边索引（1.0.9 P4）。
 *
 * 只显示当前正文能证明的出处：计划项有原句就归入正文，没有原句就留在开头清单。
 * 语义整理只作为候选标记，不合并计数、不替代出处。点击原句在编辑器里选中同一段文字。
 */

import { FileText, LocateFixed } from "lucide-react";

import type { ChapterInformationIndex, InformationOccurrence, InformationIndexItem } from "../../api/writing";

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
  activeItemId,
  onLocate,
  variant = "margin",
}: {
  index: ChapterInformationIndex;
  activeItemId?: string | null;
  onLocate: (item: InformationIndexItem, occurrence: InformationOccurrence) => void;
  variant?: "margin" | "panel";
}) {
  const prepared = index.items.filter(item => item.placement === "prepared");
  const written = index.items.filter(item => item.placement === "written");
  const candidates = index.items.filter(item => item.placement === "observed_candidate");
  const alignment = index.items.filter(item => item.placement === "alignment");
  const analysisNote = ANALYSIS_LABEL[index.analysis.state];
  return <aside className={`chapter-margin ${variant === "panel" ? "chapter-margin-panel" : ""}`} aria-label="本章信息">
    <header className="chapter-margin-header">
      <h2>本章信息</h2>
      {analysisNote && <p>{analysisNote}</p>}
    </header>
    {prepared.length > 0 && <section>
      <h3>准备写入</h3>
      <ul>
        {prepared.map(item => <li key={item.item_id} className={activeItemId === item.item_id ? "active" : ""}>
          <span className="chapter-margin-label">{item.label}</span>
        </li>)}
      </ul>
    </section>}
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
    {index.items.length === 0 && <p className="chapter-margin-empty">
      <FileText size={14} strokeWidth={1.8} aria-hidden="true" />构思中还没有明确的信息变化。返回构思，补充读者或角色在本章知道了什么。
    </p>}
  </aside>;
}
