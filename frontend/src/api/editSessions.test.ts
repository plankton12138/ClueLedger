/**
 * 修改副本客户端契约（V03-2.4）：保存必须携带保存序号，完成携带冻结序号集合。
 *
 * 这两条是「迟到响应不覆盖新文本」与「完成不丢未确认自动保存」的前端侧保证。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  currentEditSession,
  draftOf,
  finishEditSession,
  saveEditDraft,
  startEditSession,
  type EditSessionView,
} from "./editSessions";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const view: EditSessionView = {
  session: {
    session_id: "edt_1",
    project_id: "prj_1",
    base_commit_id: "commit_1",
    status: "open",
    started_at: "2026-10-18T00:00:00+00:00",
    ended_at: null,
    closed_operation_id: null,
  },
  drafts: [
    {
      session_id: "edt_1",
      chapter_id: "art_1",
      base_chapter_base: "base-1",
      text: "草稿",
      content_hash: "hash-1",
      save_seq: 3,
      updated_at: "2026-10-18T00:01:00+00:00",
      request_id: "req-3",
    },
  ],
};

describe("修改副本请求", () => {
  it("开始/恢复副本：POST 固定路径，已有副本由服务端原样返回", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      return jsonResponse(view);
    });
    vi.stubGlobal("fetch", fetchMock);

    const started = await startEditSession("prj_1");
    expect(started.session?.session_id).toBe("edt_1");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v2/projects/prj_1/edit-sessions");
  });

  it("当前副本：没有未结束副本时返回 session=null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ session: null, drafts: [] })),
    );
    const current = await currentEditSession("prj_1");
    expect(current.session).toBeNull();
    expect(current.drafts).toEqual([]);
  });

  it("保存草稿：PUT 带 expected_save_seq 与稳定 request_id", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("PUT");
      expect(JSON.parse(String(init?.body))).toEqual({
        expected_save_seq: 3,
        request_id: "req-4",
        text: "新文本",
      });
      return jsonResponse({ ...view.drafts[0], save_seq: 4, request_id: "req-4" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const saved = await saveEditDraft("prj_1", "edt_1", "art_1", {
      expectedSaveSeq: 3,
      requestId: "req-4",
      text: "新文本",
    });
    expect(saved.save_seq).toBe(4);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v2/projects/prj_1/edit-sessions/edt_1/chapters/art_1",
    );
  });

  it("完成：携带本轮冻结的保存序号集合", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        operation_id: null,
        expected_save_sequences: { art_1: 3 },
      });
      return jsonResponse({
        status: "committed",
        session_id: "edt_1",
        operation_id: "op_1",
        commit_id: "commit_2",
        changed_chapter_ids: ["art_1"],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await finishEditSession("prj_1", "edt_1", {
      expectedSaveSequences: { art_1: 3 },
    });
    expect(result.status).toBe("committed");
  });
});

describe("draftOf", () => {
  it("按章取草稿；没有草稿返回 null（按正式正文开始）", () => {
    expect(draftOf(view, "art_1")?.save_seq).toBe(3);
    expect(draftOf(view, "art_2")).toBeNull();
    expect(draftOf(null, "art_1")).toBeNull();
  });
});
