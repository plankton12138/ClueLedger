/**
 * 导入客户端契约（V03-2.3）：上传原始字节、确认携带 preview_hash 与重复决定。
 *
 * 这些断言保护的是「作者看到的预览」与「确认的请求」是同一份内容：哈希随
 * 请求发出，重复处理不会被默认值悄悄替代。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bytesToBase64,
  confirmImportPreview,
  createImportPreview,
  replacementTargets,
  updateImportPreview,
  type ImportPreview,
} from "./imports";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const preview: ImportPreview = {
  preview_id: "imp_1",
  filename: "雪山旅馆.md",
  format: "markdown",
  created_at: "2026-10-18T00:00:00+00:00",
  expires_at: "2026-10-19T00:00:00+00:00",
  revision: 1,
  batch_hash: "batch",
  chapters: [
    {
      index: 0,
      title: "第一章",
      text: "正文",
      order_key: "0001",
      content_hash: "hash-1",
    },
  ],
  duplicates: [
    {
      index: 0,
      title: "第一章",
      chapter_id: "art_1",
      existing_title: "第一章",
      existing_chapter_base: "base-1",
      content_hash: "hash-1",
    },
  ],
  selection: { mode: "append", replacements: [] },
  preview_hash: "ph-1",
};

describe("bytesToBase64", () => {
  it("按原始字节编码，多字节字符不会被截断", () => {
    const bytes = new TextEncoder().encode("雪山旅馆");
    expect(bytesToBase64(bytes)).toBe(Buffer.from("雪山旅馆", "utf-8").toString("base64"));
  });
});

describe("导入请求", () => {
  it("创建预览：上传文件名与原始字节，不先转成文本", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      const body = JSON.parse(String(init?.body)) as {
        filename: string;
        content_base64: string;
      };
      expect(body.filename).toBe("旧稿.txt");
      expect(Buffer.from(body.content_base64, "base64").toString("utf-8")).toBe("正文");
      return jsonResponse(preview, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createImportPreview("prj_1", "旧稿.txt", new TextEncoder().encode("正文"));
    expect(result.preview_id).toBe("imp_1");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v2/projects/prj_1/imports/previews");
  });

  it("调整目标：PATCH 带预期 revision 与显式替换目标", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("PATCH");
      expect(JSON.parse(String(init?.body))).toEqual({
        expected_revision: 1,
        mode: "replace",
        replacements: [{ chapter_id: "art_1", expected_chapter_base: "base-1" }],
      });
      return jsonResponse({ ...preview, revision: 2 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateImportPreview("prj_1", "imp_1", {
      expectedRevision: 1,
      mode: "replace",
      replacements: replacementTargets(preview),
    });
    expect(updated.revision).toBe(2);
  });

  it("确认：携带作者看到的 preview_hash 与重复处理决定", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        preview_hash: "ph-1",
        duplicate_decision: "replace",
      });
      return jsonResponse({
        status: "committed",
        preview_id: "imp_1",
        chapter_ids: ["art_1"],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await confirmImportPreview("prj_1", "imp_1", {
      previewHash: "ph-1",
      duplicateDecision: "replace",
    });
    expect(result.status).toBe("committed");
  });
});

describe("replacementTargets", () => {
  it("每个重复章都给出目标与当前基础（不隐式替换）", () => {
    expect(replacementTargets(preview)).toEqual([
      { chapter_id: "art_1", expected_chapter_base: "base-1" },
    ]);
  });
});
