/**
 * 来源定位（1.0.9 P5）。
 *
 * 线索详情页已撤除；账本证据仍可从对话和审查报告打开，因此保留来源读取这一条接口。
 */

import { WorkspaceApiError } from "./workspace";

export type SourceRef =
  | { kind: "file"; path: string; digest: string }
  | { kind: "module"; library_id: string; module_id: string; revision_id: string; entry_id: string; content_digest: string };

export interface SourceLocation {
  evidence_id: string;
  clue_id: string;
  source: SourceRef;
  freshness: "valid" | "stale" | "source_missing" | "unavailable" | "detached" | "invalid_range";
  quote: string;
  chapter: number | null;
  as_of_chapter: number;
  offset_unit: "unicode_codepoint";
  start: number | null;
  end: number | null;
  readable: boolean;
  current_digest: string | null;
  excerpt: string;
  excerpt_start: number;
  saved_path?: string | null;
}

export async function fetchSourceLocation(workspaceId: string, evidenceId: string, asOf: number | null): Promise<SourceLocation> {
  const params = asOf === null ? "" : `?as_of_chapter=${asOf}`;
  const response = await fetch(`/api/file-workspaces/${encodeURIComponent(workspaceId)}/source-locations/${encodeURIComponent(evidenceId)}${params}`);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error;
    throw new WorkspaceApiError(error?.code ?? `HTTP_${response.status}`, error?.why ?? "来源无法读取", error?.fix ?? "刷新线索详情后重试", response.status);
  }
  return response.json() as Promise<SourceLocation>;
}
