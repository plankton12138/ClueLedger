/**
 * 历史与证据客户端契约（V03-2.5）：恢复带当前章基础，重锚只提交引用片段。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchChapterHistory,
  fetchEvidenceLinks,
  reanchorEvidence,
  restoreChapterVersion,
} from "./history";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("版本历史", () => {
  it("按章读取，limit 进查询串", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/chapters/art_1/history?limit=5",
      );
      return jsonResponse({ versions: [{ commit_id: "c1", is_current: true }] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const versions = await fetchChapterHistory("prj_1", "art_1", 5);
    expect(versions[0].commit_id).toBe("c1");
  });

  it("恢复：带源提交与当前章基础（乐观并发）", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as {
        idempotency_key: string;
        source_commit_id: string;
        expected_chapter_base: string;
      };
      expect(body.source_commit_id).toBe("c1");
      expect(body.expected_chapter_base).toBe("base-current");
      expect(body.idempotency_key.length).toBeGreaterThan(0);
      return jsonResponse({
        status: "committed",
        commit_id: "c2",
        operation_id: "op_1",
        chapter_base: "base-new",
        source_commit_id: "c1",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await restoreChapterVersion("prj_1", "art_1", "c1", "base-current");
    expect(result.operation_id).toBe("op_1");
  });
});

describe("证据", () => {
  it("按章过滤读取", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/evidence-links?chapter_id=art_1",
      );
      return jsonResponse({ evidence: [{ evidence_id: "evd_1", status: "needs_check" }] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const items = await fetchEvidenceLinks("prj_1", "art_1");
    expect(items[0].status).toBe("needs_check");
  });

  it("重锚：只提交引用片段，不提交新状态", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({ quote: "前台声称只有一位客人" });
      return jsonResponse({
        status: "committed",
        evidence: { evidence_id: "evd_1", status: "valid" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await reanchorEvidence("prj_1", "evd_1", "前台声称只有一位客人");
    expect(result.evidence.status).toBe("valid");
  });
});
