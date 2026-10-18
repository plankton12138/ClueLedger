/**
 * 试写候选客户端（V03-3.3/3.4/3.5）：起草 → 只读预览 → 继续调整 → 按预览哈希采用。
 *
 * 预览返回的是**会被写入的完整正文**；采用必须原样带回 `preview_hash`，
 * 服务端再算一次，不一致即拒绝（迟到候选不能覆盖新稿）。
 */

import { projectPrefix, request } from "./v2";

export interface DraftBriefInput {
  target?: string;
  chapter_title?: string;
  instruction: string;
  viewpoint?: string;
  length_target?: string;
  narrative_boundary?: string;
  refs?: string[];
  idempotency_key?: string;
}

export interface CandidateRevisionSummary {
  revision: number;
  title: string;
  chars: number;
  preview_hash: string;
  created_at: string;
}

export interface ChapterCandidate {
  candidate_id: string;
  status: "ready" | "superseded" | "adopted" | "discarded";
  current_revision: number;
  brief: {
    brief_id: string;
    project_id: string;
    target: string;
    chapter_title: string;
    instruction: string;
    viewpoint: string;
    length_target: string;
    narrative_boundary: string;
    refs: string[];
    base_head: string | null;
    base_chapter: string | null;
  };
  title: string;
  text: string;
  preview_hash: string;
  model: string;
  created_at: string;
  updated_at: string;
  adopted_operation_id: string | null;
  adopted_chapter_id: string | null;
  revisions: CandidateRevisionSummary[];
}

export interface CandidateSummary {
  candidate_id: string;
  status: string;
  current_revision: number;
  title: string;
  excerpt: string;
  chars: number;
  target: string;
  preview_hash: string;
  updated_at: string;
  adopted_chapter_id: string | null;
}

export interface DraftAdoptResult {
  status: string;
  candidate_id: string;
  chapter_id: string;
  operation_id: string;
  commit_id?: string | null;
  changed_chapter_ids?: string[];
}

export async function createDraft(
  projectId: string,
  brief: DraftBriefInput,
): Promise<ChapterCandidate> {
  return request(`${projectPrefix(projectId)}/drafts`, {
    target: brief.target ?? "new",
    chapter_title: brief.chapter_title ?? "",
    instruction: brief.instruction,
    viewpoint: brief.viewpoint ?? "",
    length_target: brief.length_target ?? "",
    narrative_boundary: brief.narrative_boundary ?? "",
    refs: brief.refs ?? [],
    idempotency_key: brief.idempotency_key ?? null,
  });
}

export async function fetchDraft(
  projectId: string,
  candidateId: string,
): Promise<ChapterCandidate> {
  return request(
    `${projectPrefix(projectId)}/drafts/${encodeURIComponent(candidateId)}`,
  );
}

export async function listDrafts(projectId: string): Promise<CandidateSummary[]> {
  const payload = await request<{ candidates: CandidateSummary[] }>(
    `${projectPrefix(projectId)}/drafts`,
  );
  return payload.candidates;
}

export async function reviseDraft(
  projectId: string,
  candidateId: string,
  body: { instruction: string; expectedRevision?: number },
): Promise<ChapterCandidate> {
  return request(
    `${projectPrefix(projectId)}/drafts/${encodeURIComponent(candidateId)}/revise`,
    {
      instruction: body.instruction,
      expected_revision: body.expectedRevision ?? null,
    },
  );
}

export async function adoptDraft(
  projectId: string,
  candidateId: string,
  previewHash: string,
): Promise<DraftAdoptResult> {
  return request(
    `${projectPrefix(projectId)}/drafts/${encodeURIComponent(candidateId)}/adopt`,
    { preview_hash: previewHash },
  );
}

export async function discardDraft(
  projectId: string,
  candidateId: string,
): Promise<ChapterCandidate> {
  return request(
    `${projectPrefix(projectId)}/drafts/${encodeURIComponent(candidateId)}/discard`,
    {},
  );
}
