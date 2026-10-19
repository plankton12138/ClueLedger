/**
 * 操作影响客户端契约（V03-7.7）：差异依据、后续修改与关联采用。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchOperationImpact } from "./impact";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("操作影响", () => {
  it("按操作读取实际差异与后续修改标记", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/operations/op_1/impact",
      );
      return jsonResponse({
        operation_id: "op_1",
        kind: "revision.publish",
        commit_id: "c2",
        before_commit_id: "c1",
        head_commit_id: "c2",
        basis: "version_diff",
        note: "高亮只表示实际版本差异",
        chapters: [
          {
            chapter_id: "art_ch1",
            title: "第一章",
            chars_before: 10,
            chars_after: 12,
            chars_current: 12,
            hunks: 1,
            inserted_chars: 4,
            deleted_chars: 2,
            has_later_changes: false,
            basis: "version_diff",
          },
        ],
        ledger: [],
        highlights: [
          { target_id: "art_ch1", kind: "chapter", basis: "version_diff", hunks: 1 },
        ],
        related_acceptances: [],
        continuation: {
          chapter_ids: ["art_ch1"],
          has_later_changes: false,
          blocked_by_edit_session: false,
          note: "从这些章继续创作",
        },
        refs: {},
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const impact = await fetchOperationImpact("prj_1", "op_1");
    expect(impact.basis).toBe("version_diff");
    expect(impact.chapters[0].inserted_chars).toBe(4);
    expect(impact.highlights[0].kind).toBe("chapter");
    expect(impact.continuation.chapter_ids).toEqual(["art_ch1"]);
  });
});
