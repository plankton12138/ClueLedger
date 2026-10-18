/**
 * 手工修改副本客户端（V03-2.4 / M04）。
 *
 * 草稿是独立工作数据：保存只写副本，不移动正式正文；「完成」才整批发布一次。
 * 保存必须带 `expected_save_seq`——服务端用它拒绝迟到的旧请求。
 */

import { projectPrefix, request } from "./v2";

export interface EditSessionWire {
  session_id: string;
  project_id: string;
  base_commit_id: string | null;
  status: "open" | "closed" | "abandoned";
  started_at: string;
  ended_at: string | null;
  closed_operation_id: string | null;
}

export interface DraftChapterWire {
  session_id: string;
  chapter_id: string;
  base_chapter_base: string;
  text: string;
  content_hash: string;
  save_seq: number;
  updated_at: string;
  request_id: string;
}

export interface EditSessionView {
  session: EditSessionWire | null;
  drafts: DraftChapterWire[];
}

export interface FinishEditResult {
  status: string;
  session_id: string;
  operation_id: string;
  commit_id: string | null;
  changed_chapter_ids: string[];
}

export async function currentEditSession(projectId: string): Promise<EditSessionView> {
  return request(`${projectPrefix(projectId)}/edit-sessions/current`);
}

export async function startEditSession(projectId: string): Promise<EditSessionView> {
  return request(`${projectPrefix(projectId)}/edit-sessions`, {});
}

export async function saveEditDraft(
  projectId: string,
  sessionId: string,
  chapterId: string,
  body: { expectedSaveSeq: number; requestId: string; text: string },
): Promise<DraftChapterWire> {
  return request(
    `${projectPrefix(projectId)}/edit-sessions/${encodeURIComponent(sessionId)}/chapters/${encodeURIComponent(chapterId)}`,
    {
      expected_save_seq: body.expectedSaveSeq,
      request_id: body.requestId,
      text: body.text,
    },
    "PUT",
  );
}

export async function suspendEditSession(
  projectId: string,
  sessionId: string,
): Promise<EditSessionView> {
  return request(
    `${projectPrefix(projectId)}/edit-sessions/${encodeURIComponent(sessionId)}/suspend`,
    {},
  );
}

export async function abandonEditSession(
  projectId: string,
  sessionId: string,
): Promise<EditSessionView> {
  return request(
    `${projectPrefix(projectId)}/edit-sessions/${encodeURIComponent(sessionId)}/abandon`,
    {},
  );
}

export async function finishEditSession(
  projectId: string,
  sessionId: string,
  body: { expectedSaveSequences: Record<string, number>; operationId?: string },
): Promise<FinishEditResult> {
  return request(
    `${projectPrefix(projectId)}/edit-sessions/${encodeURIComponent(sessionId)}/finish`,
    {
      operation_id: body.operationId ?? null,
      expected_save_sequences: body.expectedSaveSequences,
    },
  );
}

/** 当前章草稿；没有草稿返回 null（按正式正文开始修改）。 */
export function draftOf(
  view: EditSessionView | null,
  chapterId: string,
): DraftChapterWire | null {
  return view?.drafts.find((draft) => draft.chapter_id === chapterId) ?? null;
}
