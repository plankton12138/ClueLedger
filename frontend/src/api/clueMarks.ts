import { request } from "./workspace";

export type ClueMarkRole = "plant" | "mention" | "resolve";

export interface ClueMark {
  clue_id: string;
  title: string;
  start: number;
  end: number;
  quote: string;
  role: ClueMarkRole;
  mode: string;
  chapter: number;
  source?: "ledger" | "anchor";
}

export interface ClueMarks {
  path: string;
  digest: string;
  state: "ready" | "empty" | "stale" | "unrecorded";
  marks: ClueMark[];
  ledger_revision: string;
}

export function fetchClueMarks(
  workspaceId: string,
  chapterPath: string,
  asOfChapter: number | null,
): Promise<ClueMarks> {
  const params = new URLSearchParams({ chapter_path: chapterPath });
  if (asOfChapter !== null) params.set("as_of_chapter", String(asOfChapter));
  return request<ClueMarks>(
    `/${workspaceId}/clue-marks?${params.toString()}`,
  );
}
