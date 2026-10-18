/**
 * 试写客户端契约（V03-3.4）：采用必须原样带回预览哈希，调整必须带修订号。
 *
 * 这两个字段是「所见即所采」与「迟到候选不覆盖新稿」在客户端的唯一凭据；
 * 任何默认值填补都会让服务端校验失去意义。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  adoptDraft,
  createDraft,
  fetchDraft,
  fetchLedger,
  listDrafts,
  reviseDraft,
  type ChapterCandidate,
} from "./drafting";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const candidate: ChapterCandidate = {
  candidate_id: "cnd_1",
  status: "ready",
  current_revision: 2,
  brief: {
    brief_id: "drf_1",
    project_id: "p1",
    target: "new",
    chapter_title: "雪山旅馆",
    instruction: "林昭住进旅馆",
    viewpoint: "第三人称限知",
    length_target: "2000 字",
    narrative_boundary: "不揭示凶手",
    refs: ["chr_1"],
    base_head: null,
    base_chapter: null,
  },
  title: "雪山旅馆",
  text: "第一段。\n\n第二段。",
  preview_hash: "abc123",
  model: "test-model",
  created_at: "2026-10-18T00:00:00+00:00",
  updated_at: "2026-10-18T00:01:00+00:00",
  adopted_operation_id: null,
  adopted_chapter_id: null,
  revisions: [
    {
      revision: 2,
      title: "雪山旅馆",
      chars: 10,
      preview_hash: "abc123",
      created_at: "2026-10-18T00:01:00+00:00",
    },
  ],
};

describe("drafting api", () => {
  it("createDraft 发送目标、要求与引用设定", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(init?.method).toBe("POST");
      expect(body.target).toBe("new");
      expect(body.instruction).toBe("林昭住进旅馆");
      expect(body.refs).toEqual(["chr_1"]);
      expect(body.idempotency_key).toBe("k1");
      return jsonResponse(candidate);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createDraft("p1", {
      instruction: "林昭住进旅馆",
      refs: ["chr_1"],
      idempotency_key: "k1",
    });
    expect(result.candidate_id).toBe("cnd_1");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("reviseDraft 携带修订号，服务端据此拒绝过期调整", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body.expected_revision).toBe(2);
      expect(body.instruction).toBe("更冷一些");
      return jsonResponse(candidate);
    });
    vi.stubGlobal("fetch", fetchMock);

    await reviseDraft("p1", "cnd_1", { instruction: "更冷一些", expectedRevision: 2 });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("adoptDraft 只发预览哈希，不带正文副本", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toContain("/drafts/cnd_1/adopt");
      const body = JSON.parse(String(init?.body));
      expect(body).toEqual({ preview_hash: "abc123" });
      return jsonResponse({
        status: "committed",
        candidate_id: "cnd_1",
        chapter_id: "ch_1",
        operation_id: "op_1",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await adoptDraft("p1", "cnd_1", "abc123");
    expect(result.chapter_id).toBe("ch_1");
    expect(result.operation_id).toBe("op_1");
  });

  it("fetchDraft 与 listDrafts 走只读端点", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/drafts")) {
        return jsonResponse({
          candidates: [
            {
              candidate_id: "cnd_1",
              status: "ready",
              current_revision: 2,
              title: "雪山旅馆",
              excerpt: "第一段。",
              chars: 10,
              target: "new",
              preview_hash: "abc123",
              updated_at: "2026-10-18T00:01:00+00:00",
              adopted_chapter_id: null,
            },
          ],
        });
      }
      return jsonResponse(candidate);
    });
    vi.stubGlobal("fetch", fetchMock);

    expect((await fetchDraft("p1", "cnd_1")).text).toContain("第一段");
    const rows = await listDrafts("p1");
    expect(rows[0].candidate_id).toBe("cnd_1");
    expect(rows[0].excerpt).toBe("第一段。");
  });

  it("fetchLedger 返回可引用条目", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        entries: [
          {
            id: "chr_1",
            kind: "canon.character",
            kind_label: "人物",
            title: "林昭",
            summary: "侦探",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const entries = await fetchLedger("p1");
    expect(entries).toHaveLength(1);
    expect(entries[0].kind_label).toBe("人物");
  });
});
