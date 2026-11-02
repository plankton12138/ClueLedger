/**
 * 构思改动的读取与撤销（1.0.9 P5）。
 *
 * 新写作页的构思编辑走工作台接口；对话里的提交卡片仍需读取改动记录并支持撤销。
 * 旧方案图、时间轴、同步、正文标记与本章聚合页面已撤除，不再保留对应客户端。
 */

import { request } from "./workspace";

export interface StoryReceipt {
  variant_id: string;
  base_revision: string;
  revision: string;
  change_id: string;
  request_key: string;
  result: Record<string, unknown>;
  changed: string[];
}

export interface StoryChangeRecord {
  change_id: string;
  variant_id: string;
  base_revision: string;
  revision: string;
  created_at: string;
  operations: Record<string, unknown>[];
  changed: string[];
  undone_by: string | null;
}

export function readStoryChange(
  workspaceId: string,
  changeId: string,
): Promise<StoryChangeRecord> {
  return request<StoryChangeRecord>(
    `/${workspaceId}/story/changes/${encodeURIComponent(changeId)}`,
  );
}

export function undoStoryChange(
  workspaceId: string,
  changeId: string,
  requestKey: string,
): Promise<StoryReceipt> {
  return request<StoryReceipt>(
    `/${workspaceId}/story/changes/${encodeURIComponent(changeId)}/undo?request_key=${encodeURIComponent(requestKey)}`,
    { method: "POST" },
  );
}
