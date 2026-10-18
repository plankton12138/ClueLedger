/**
 * 章版本历史与证据核对客户端（V03-2.5）。
 *
 * 历史只读；恢复是一次新发布（带幂等键与当前章基础）。证据重锚只改关联，
 * 引用片段必须由作者从当前正文中给出。
 */

import { projectPrefix, request } from "./v2";

export interface ChapterVersion {
  commit_id: string;
  created_at: string;
  summary: string;
  actor: string;
  chapter_base: string;
  title: string;
  preview: string;
  char_count: number;
  is_current: boolean;
}

export interface RestoreResult {
  status: string;
  commit_id: string | null;
  operation_id: string;
  chapter_base: string;
  source_commit_id: string;
}

export interface EvidenceLinkWire {
  artifact_id: string;
  evidence_id: string;
  anchor_kind: string;
  target_id: string;
  chapter_id: string;
  quote: string;
  source_hash: string;
  dependencies: string[];
  status: "valid" | "needs_check";
  reason: string;
  previous: Record<string, unknown> | null;
}

export async function fetchChapterHistory(
  projectId: string,
  chapterId: string,
  limit = 20,
): Promise<ChapterVersion[]> {
  const body = await request<{ versions: ChapterVersion[] }>(
    `${projectPrefix(projectId)}/chapters/${encodeURIComponent(chapterId)}/history?limit=${limit}`,
  );
  return body.versions;
}

export async function restoreChapterVersion(
  projectId: string,
  chapterId: string,
  sourceCommitId: string,
  expectedChapterBase: string,
): Promise<RestoreResult> {
  return request(
    `${projectPrefix(projectId)}/chapters/${encodeURIComponent(chapterId)}/restore`,
    {
      idempotency_key: crypto.randomUUID(),
      source_commit_id: sourceCommitId,
      expected_chapter_base: expectedChapterBase,
    },
  );
}

export async function fetchEvidenceLinks(
  projectId: string,
  chapterId: string,
): Promise<EvidenceLinkWire[]> {
  const body = await request<{ evidence: EvidenceLinkWire[] }>(
    `${projectPrefix(projectId)}/evidence-links?chapter_id=${encodeURIComponent(chapterId)}`,
  );
  return body.evidence;
}

export async function reanchorEvidence(
  projectId: string,
  evidenceId: string,
  quote: string,
): Promise<{ status: string; evidence: EvidenceLinkWire }> {
  return request(
    `${projectPrefix(projectId)}/evidence-links/${encodeURIComponent(evidenceId)}/reanchor`,
    { quote },
  );
}
