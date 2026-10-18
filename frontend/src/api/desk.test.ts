/**
 * 工作台共享状态客户端（V03-4.5）：计数与最近结果同源，不复制列表。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchDeskState, pendingLabel, toOperationEnvelope } from "./desk";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const STATE = {
  project_id: "prj_1",
  commit_id: "cmt_1",
  counts: {
    ledger_entries: 3,
    agreements: 1,
    summaries: { fresh: 1, stale: 2, missing: 0 },
    pending_candidates: 2,
    hypothesis_candidates: 1,
    open_edit_chapters: 0,
  },
  recent_operations: [
    {
      operation_id: "op_1",
      kind: "chapter.import",
      status: "committed",
      conversation_id: "conv_1",
      changed_chapter_ids: ["art_1", "art_2"],
      created_at: "2026-10-18T08:00:00+00:00",
    },
  ],
  refs: {
    candidate_group_id: "lcg_1",
    candidate_revision: 3,
    agreements_hash: "abc",
    edit_session_id: null,
    edit_chapter_ids: [],
  },
  content_hash: "hash-1",
};

describe("工作台共享状态", () => {
  it("按作品读取计数与权威 ID", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v2/projects/prj_1/desk-state");
      return jsonResponse(STATE);
    });
    vi.stubGlobal("fetch", fetchMock);

    const state = await fetchDeskState("prj_1");
    expect(state.counts.pending_candidates).toBe(2);
    expect(state.refs.candidate_group_id).toBe("lcg_1");
    // 只给计数与引用：正文一律回候选组/章节接口取。
    expect(state).not.toHaveProperty("items");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("最近结果转成与实时事件同一形状", () => {
    const envelope = toOperationEnvelope("prj_1", STATE.recent_operations[0]);
    expect(envelope.operationId).toBe("op_1");
    expect(envelope.changedChapterIds).toEqual(["art_1", "art_2"]);
    expect(envelope.conversationId).toBe("conv_1");
    expect(envelope.status).toBe("committed");
  });

  it("待确认数量为 0 时不显示入口", () => {
    expect(pendingLabel(0)).toBe("");
    expect(pendingLabel(2)).toBe("待确认 2");
  });
});
