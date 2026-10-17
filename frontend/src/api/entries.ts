/**
 * 无作品入口客户端（M02/V03-1.2）——`POST /api/v2/entries` 的 save_idea 模式。
 *
 * 后端幂等语义（entry_service.py）：
 * - `entry_id` 确定性派生作品 ID——同一入口（浏览器）的所有想法落在同一作品；
 *   入口身份生成一次、持久复用；
 * - `request_id` 按次分配：首次提交生成，失败重试复用同一 request_id
 *   （服务端按 request_id+文本哈希去重）；保存成功清除，同文本再保存
 *   是作者明确的第二次想法。
 *
 * 纯函数接收可注入存储，node 环境可测（项目偏好：不引入 jsdom）。
 */

import { request } from "./v2";

export interface SaveIdeaInput {
  entryId: string;
  requestId: string;
  text: string;
  title?: string;
}

/** POST /api/v2/entries 响应（server/v2.py create_entry）。 */
export interface EntrySavedWire {
  entry_id: string;
  project_id: string;
  conversation_id: string | null;
  message_id: string;
  status: string;
}

export async function saveIdea(input: SaveIdeaInput): Promise<EntrySavedWire> {
  return request<EntrySavedWire>("/api/v2/entries", {
    entry_id: input.entryId,
    request_id: input.requestId,
    text: input.text,
    mode: "save_idea",
    title: input.title ?? "",
  });
}

// ---------------------------------------------------------------------------
// 入口身份与幂等键（纯函数 + 可注入存储）
// ---------------------------------------------------------------------------

export type DraftIdentityStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

const ENTRY_ID_KEY = "hastings.desk.entryId";
const PENDING_KEY_PREFIX = "hastings.desk.ideaRequest:";

/** 作品名取想法首行（首保存建作品时使用；服务端不覆盖已有显示名）。 */
export function ideaTitle(text: string): string {
  const firstLine = text.trim().split("\n")[0] ?? "";
  return firstLine === "" ? "新想法" : firstLine.slice(0, 12);
}

/** FNV-1a 32 位内容指纹：只为待重试键配对，不作安全用途。 */
export function draftFingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * 入口身份：同一浏览器生成一次并持久复用（entry_id → 同一作品）。
 * 存储禁用时退化为会话内身份，功能可用，只是刷新后换新作品。
 */
export function readStableEntryId(
  storage: DraftIdentityStorage = localStorage,
): string {
  try {
    const stored = storage.getItem(ENTRY_ID_KEY);
    if (stored !== null && stored !== "") return stored;
  } catch {
    /* 落到会话内生成 */
  }
  const entryId = `ent_${crypto.randomUUID()}`;
  try {
    storage.setItem(ENTRY_ID_KEY, entryId);
  } catch {
    /* 同上 */
  }
  return entryId;
}

function pendingKey(text: string): string {
  return PENDING_KEY_PREFIX + draftFingerprint(text.trim());
}

/** 读取该草稿待重试的 request_id；没有（首次提交）返回 null。 */
export function readPendingRequestId(
  text: string,
  storage: DraftIdentityStorage = localStorage,
): string | null {
  try {
    return storage.getItem(pendingKey(text));
  } catch {
    return null;
  }
}

/** 记录待重试的 request_id（首次提交时调用）；重试路径复用。 */
export function writePendingRequestId(
  text: string,
  requestId: string,
  storage: DraftIdentityStorage = localStorage,
): void {
  try {
    storage.setItem(pendingKey(text), requestId);
  } catch {
    /* 禁用存储时退化为每次新键：首次保存仍成功，只是失去重试幂等。 */
  }
}

/** 保存成功后清除：同文本再保存是作者明确的第二次想法（新 request_id）。 */
export function clearPendingRequestId(
  text: string,
  storage: DraftIdentityStorage = localStorage,
): void {
  try {
    storage.removeItem(pendingKey(text));
  } catch {
    /* 同上，忽略。 */
  }
}

export function newRequestId(): string {
  return `req_${crypto.randomUUID()}`;
}

/** 保存草稿文本（刷新/重启恢复，M02「独立草稿」）。 */
const DRAFT_KEY = "hastings.desk.ideaDraft";

export function readIdeaDraft(storage: DraftIdentityStorage = localStorage): string {
  try {
    return storage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function writeIdeaDraft(
  text: string,
  storage: DraftIdentityStorage = localStorage,
): void {
  try {
    if (text === "") storage.removeItem(DRAFT_KEY);
    else storage.setItem(DRAFT_KEY, text);
  } catch {
    /* 同上。 */
  }
}
