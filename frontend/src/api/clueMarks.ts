import { request } from "./workspace";

export type ClueKind = "truth" | "mislead";

export interface ClueMark {
  clue_id: string;
  title: string;
  /** 作者希望的用途；null 表示还没决定。 */
  kind: ClueKind | null;
  start: number;
  end: number;
  quote: string;
  mode: string;
  chapter: number;
  sources: ("binding" | "ledger")[];
}

export interface InvalidClueMark {
  clue_id: string;
  title: string;
  quote: string;
  reason: "missing" | "ambiguous" | "unavailable";
}

export interface ClueMarks {
  path: string;
  digest: string;
  state: "ready" | "empty" | "stale" | "unrecorded";
  marks: ClueMark[];
  invalid: InvalidClueMark[];
  ledger_revision: string;
  variant_revision: string;
}

export function fetchClueMarks(
  workspaceId: string,
  chapterPath: string,
  asOfChapter: number | null,
  variantId?: string | null,
): Promise<ClueMarks> {
  const params = new URLSearchParams({ chapter_path: chapterPath });
  if (asOfChapter !== null) params.set("as_of_chapter", String(asOfChapter));
  if (variantId) params.set("variant_id", variantId);
  return request<ClueMarks>(`/${workspaceId}/clue-marks?${params.toString()}`);
}
