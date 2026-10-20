/**
 * 正文导入客户端（V03-2.3 / M06）：上传预览 → 调整目标 → 整批确认。
 *
 * 预览只解析不写正文；确认必须带作者看到的 `preview_hash`。上传的是**原始字节**
 * 的 base64——编码错误由服务端如实报告（不在浏览器里猜测转码）。
 */

import { projectPrefix, request } from "./v2";

export interface ImportChapterPreview {
  index: number;
  title: string;
  text: string;
  order_key: string;
  content_hash: string;
}

export interface ImportDuplicate {
  index: number;
  title: string;
  chapter_id: string;
  existing_title: string;
  existing_chapter_base: string;
  content_hash: string;
}

export interface ImportReplacement {
  chapter_id: string;
  expected_chapter_base: string;
  index?: number;
}

export interface ImportPreview {
  preview_id: string;
  filename: string;
  format: string;
  created_at: string;
  expires_at: string;
  revision: number;
  batch_hash: string;
  chapters: ImportChapterPreview[];
  duplicates: ImportDuplicate[];
  selection: { mode: "append" | "replace"; replacements: ImportReplacement[] };
  preview_hash: string;
}

export interface ImportConfirmResult {
  status: string;
  preview_id: string;
  project_id?: string;
  operation_id?: string;
  commit_id?: string | null;
  changed_chapter_ids?: string[];
  chapter_ids: string[];
}

export type DuplicateDecision = "cancel" | "append" | "replace";

/** 原始字节 → base64（按块处理，避免大文件展开成超长参数）。 */
export function bytesToBase64(bytes: Uint8Array): string {
  const chunk = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

export async function createImportPreview(
  projectId: string,
  filename: string,
  bytes: Uint8Array,
): Promise<ImportPreview> {
  return request(`${projectPrefix(projectId)}/imports/previews`, {
    filename,
    content_base64: bytesToBase64(bytes),
  });
}

export async function updateImportPreview(
  projectId: string,
  previewId: string,
  body: {
    expectedRevision: number;
    mode: "append" | "replace";
    replacements: ImportReplacement[];
  },
): Promise<ImportPreview> {
  return request(
    `${projectPrefix(projectId)}/imports/previews/${encodeURIComponent(previewId)}`,
    {
      expected_revision: body.expectedRevision,
      mode: body.mode,
      replacements: body.replacements,
    },
    "PATCH",
  );
}

export async function confirmImportPreview(
  projectId: string,
  previewId: string,
  body: { previewHash: string; duplicateDecision?: DuplicateDecision },
): Promise<ImportConfirmResult> {
  return request(
    `${projectPrefix(projectId)}/imports/previews/${encodeURIComponent(previewId)}/confirm`,
    {
      preview_hash: body.previewHash,
      duplicate_decision: body.duplicateDecision ?? null,
    },
  );
}

/** 替换全部重复章的目标：显式逐章给出目标与当前基础（计划 M06 第 2 条）。 */
export function replacementTargets(preview: ImportPreview): ImportReplacement[] {
  return preview.duplicates.map((duplicate) => ({
    chapter_id: duplicate.chapter_id,
    expected_chapter_base: duplicate.existing_chapter_base,
  }));
}
