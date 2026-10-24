/**
 * 灵感本客户端（0.8 C）。
 *
 * 条目、修订与删除恢复都走作品前缀；正文按修订读取。这里只做字段映射，不在前端
 * 猜测冲突或自动合并——并发编辑由服务端返回的版本号判定。
 */

import { WorkspaceApiError } from "./workspace";

const WORKSPACES = "/api/file-workspaces";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : "网络请求失败");
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const error = (body as { error?: Record<string, unknown> } | null)?.error;
    if (error) {
      throw new WorkspaceApiError(
        String(error.code ?? ""),
        String(error.why ?? ""),
        String(error.fix ?? ""),
        response.status,
      );
    }
    throw new Error(`请求失败：${response.status}`);
  }
  return (await response.json()) as T;
}

export interface IdeaSummary {
  idea_id: string;
  head_revision_id: string;
  title: string;
  excerpt: string;
  tags: string[];
  deleted: boolean;
  updated_at: string;
}

export interface IdeaView {
  idea_id: string;
  head_revision_id: string;
  revision_id: string;
  parent: string | null;
  created_at: string;
  updated_at: string;
  title: string;
  body: string;
  tags: string[];
  deleted: boolean;
  operation: string;
  restored_from: string | null;
  origin: Record<string, unknown> | null;
  uses: Record<string, unknown>[];
  digest: string;
}

export interface IdeaRevision {
  id: string;
  parent: string | null;
  created_at: string;
  operation: string;
  restored_from: string | null;
  deleted: boolean;
  title: string;
  chars: number;
}

export interface IdeaPage {
  items: IdeaSummary[];
  total: number;
  invalid: number;
  next_cursor: string | null;
}

export interface RevisionPage {
  items: IdeaRevision[];
  total: number;
  next_cursor: string | null;
}

export function listIdeas(
  workspaceId: string,
  options: { q?: string; deleted?: boolean; cursor?: string | null; limit?: number } = {},
): Promise<IdeaPage> {
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.deleted) params.set("deleted", "true");
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.limit) params.set("limit", String(options.limit));
  const suffix = params.toString();
  return request<IdeaPage>(`${WORKSPACES}/${workspaceId}/ideas${suffix ? `?${suffix}` : ""}`);
}

export function getIdea(workspaceId: string, ideaId: string): Promise<IdeaView> {
  return request<IdeaView>(`${WORKSPACES}/${workspaceId}/ideas/${ideaId}`);
}

export function createIdea(
  workspaceId: string,
  input: { title: string; body: string; tags: string[]; request_key: string },
): Promise<IdeaView> {
  return request<IdeaView>(`${WORKSPACES}/${workspaceId}/ideas`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateIdea(
  workspaceId: string,
  ideaId: string,
  input: {
    expected_digest: string;
    expected_revision: string;
    title?: string;
    body?: string;
    tags?: string[];
    request_key: string;
  },
): Promise<IdeaView> {
  return request<IdeaView>(`${WORKSPACES}/${workspaceId}/ideas/${ideaId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function trashIdea(
  workspaceId: string,
  ideaId: string,
  input: { expected_digest: string; expected_revision: string; request_key: string },
): Promise<IdeaView> {
  return request<IdeaView>(`${WORKSPACES}/${workspaceId}/ideas/${ideaId}/trash`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function restoreIdea(
  workspaceId: string,
  ideaId: string,
  input: { target_revision: string; expected_digest: string; request_key: string },
): Promise<IdeaView> {
  return request<IdeaView>(`${WORKSPACES}/${workspaceId}/ideas/${ideaId}/restore`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listIdeaRevisions(
  workspaceId: string,
  ideaId: string,
  options: { cursor?: string | null; limit?: number } = {},
): Promise<RevisionPage> {
  const params = new URLSearchParams();
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.limit) params.set("limit", String(options.limit));
  const suffix = params.toString();
  return request<RevisionPage>(
    `${WORKSPACES}/${workspaceId}/ideas/${ideaId}/revisions${suffix ? `?${suffix}` : ""}`,
  );
}

export function getIdeaRevision(
  workspaceId: string,
  ideaId: string,
  revisionId: string,
): Promise<{
  idea_id: string;
  revision_id: string;
  parent: string | null;
  created_at: string;
  operation: string;
  restored_from: string | null;
  title: string;
  body: string;
  tags: string[];
  deleted: boolean;
}> {
  return request(`${WORKSPACES}/${workspaceId}/ideas/${ideaId}/revisions/${revisionId}`);
}

export function requestKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}
