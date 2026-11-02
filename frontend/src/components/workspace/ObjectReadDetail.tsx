/**
 * 构思与线索共用的只读详情。
 *
 * 页面只呈现当前结果：标题、作者安排、已登记出处和必要提醒。没有字段输入、保存或分类
 * 按钮；调整统一从「讨论调整」进入对话。没有值的字段不渲染空格子。
 */

import type { ReactNode } from "react";
import { Locate, MessageSquare, X } from "lucide-react";

import { Button } from "../ui/Button";

export interface ReadDetailOccurrence {
  key: string;
  path: string;
  quote: string;
  state: "exact" | "relocated" | "missing" | "ambiguous" | "unavailable" | string;
  chapter?: number | null;
  /** 传给定位回调的账本依据；没有时按路径与引文定位。 */
  evidenceId?: string;
}

export interface ReadDetailSection {
  key: string;
  title: string;
  text: string;
}

const STATE_LABEL: Record<string, string> = {
  exact: "可定位",
  relocated: "已重新定位",
  missing: "原句已不在正文",
  ambiguous: "原句有多处匹配",
  unavailable: "正文暂不可读",
  unchecked: "尚未核对",
  aligned: "与正文一致",
  outline_ahead: "构思在上次核对后改过",
  prose_ahead: "正文在上次核对后改过",
  diverged: "构思与正文都改过",
};

const LOCKED_STATES = new Set(["missing", "ambiguous", "unavailable"]);

function withoutEmpty<T extends { text?: string | null }>(rows: T[]): T[] {
  return rows.filter(row => row.text !== undefined && row.text !== null && row.text.trim().length > 0);
}

export default function ObjectReadDetail({
  title,
  kindLabel,
  statusLabel,
  reasons = [],
  sections,
  occurrences,
  occurrencesTitle = "已登记出处",
  emptyOccurrences = "暂无已登记正文出处",
  notice,
  extra,
  onLocate,
  onDiscuss,
  onClose,
}: {
  title: string;
  kindLabel?: string;
  statusLabel?: string;
  reasons?: string[];
  sections: ReadDetailSection[];
  occurrences: ReadDetailOccurrence[];
  occurrencesTitle?: string;
  emptyOccurrences?: string;
  notice?: string;
  /** 关系等只读补充内容；由调用方决定排版。 */
  extra?: ReactNode;
  onLocate: (occurrence: ReadDetailOccurrence) => void;
  onDiscuss: () => void;
  onClose: () => void;
}) {
  const visibleSections = withoutEmpty(sections);
  return (
    <article className="workspace-detail" aria-label={`${title}详情`}>
      <header className="workspace-detail-head">
        <div>
          <h2>{title}</h2>
          {kindLabel && <p className="workspace-detail-status">{kindLabel}</p>}
          {statusLabel && (
            <p className={`workspace-detail-status ${statusLabel === "需核对" ? "attention" : statusLabel === "草稿" ? "draft" : "ready"}`}>
              {statusLabel}
              {reasons.length > 0 ? ` · ${reasons.join("、")}` : ""}
            </p>
          )}
        </div>
        <Button size="compact" aria-label="关闭详情" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </Button>
      </header>

      {visibleSections.map(section => (
        <section key={section.key} className="workspace-detail-section" aria-label={section.title}>
          <h3>{section.title}</h3>
          <p className="workspace-detail-text">{section.text}</p>
        </section>
      ))}

      {notice && <p className="workspace-detail-hint">{notice}</p>}

      <section className="workspace-detail-section" aria-label={occurrencesTitle}>
        <h3>{occurrencesTitle}</h3>
        {occurrences.length === 0 ? (
          <p className="workspace-detail-hint">{emptyOccurrences}</p>
        ) : (
          <ul className="workspace-detail-list">
            {occurrences.map(row => (
              <li key={row.key}>
                <span className="workspace-detail-quote">{row.quote}</span>
                <span className="workspace-detail-meta">
                  {row.chapter ? `第 ${row.chapter} 章 · ` : ""}
                  {STATE_LABEL[row.state] ?? row.state}
                </span>
                {!LOCKED_STATES.has(row.state) && (
                  <Button size="compact" variant="quiet" onClick={() => onLocate(row)}>
                    <Locate size={14} aria-hidden="true" />定位原文
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {extra}

      <div className="workspace-detail-actions">
        <Button size="compact" variant="primary" onClick={onDiscuss}>
          <MessageSquare size={15} aria-hidden="true" />讨论调整
        </Button>
      </div>
    </article>
  );
}
