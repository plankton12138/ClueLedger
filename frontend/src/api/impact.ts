/**
 * 操作影响客户端（V03-7.7）：按实际版本差异回读改动范围与后续修改。
 *
 * 只读；高亮只说明差异，不代表修订完成、质量分数或嫌疑概率。
 */

import { projectPrefix, request } from "./v2";

export interface ImpactChapterWire {
  chapter_id: string;
  title: string;
  chars_before: number;
  chars_after: number;
  chars_current: number;
  hunks: number;
  inserted_chars: number;
  deleted_chars: number;
  has_later_changes: boolean;
  basis: "version_diff";
}

export interface ImpactLedgerWire {
  artifact_id: string;
  kind: string;
  title: string;
  changed_fields: string[];
  before_revision: string | null;
  after_revision: string;
  current_revision: string | null;
  has_later_changes: boolean;
  basis: "version_diff";
}

export interface ImpactHighlightWire {
  target_id: string;
  kind: "chapter" | "ledger";
  basis: "version_diff";
  hunks?: number;
  fields?: string[];
}

export interface RelatedAcceptanceWire {
  commit_id: string;
  request_id: string;
  item_ids: string[];
  at: string;
}

export interface ImpactContinuationWire {
  chapter_ids: string[];
  has_later_changes: boolean;
  blocked_by_edit_session: boolean;
  note: string;
}

export interface ImpactWire {
  operation_id: string;
  kind: string;
  commit_id: string | null;
  before_commit_id: string | null;
  head_commit_id: string | null;
  basis: "version_diff";
  note: string;
  chapters: ImpactChapterWire[];
  ledger: ImpactLedgerWire[];
  highlights: ImpactHighlightWire[];
  related_acceptances: RelatedAcceptanceWire[];
  continuation: ImpactContinuationWire;
  refs: Record<string, unknown>;
}

export async function fetchOperationImpact(
  projectId: string,
  operationId: string,
): Promise<ImpactWire> {
  return request(
    `${projectPrefix(projectId)}/operations/${encodeURIComponent(operationId)}/impact`,
  );
}
