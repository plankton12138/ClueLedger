/**
 * 后续状态客户端（V03-2.6）：只读操作查询；状态文案不冒充「已执行」。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchOperation,
  suggestionsLabel,
  summaryLabel,
} from "./followups";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("操作后续状态", () => {
  it("按操作 ID 读取状态", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v2/projects/prj_1/operations/op_1");
      return jsonResponse({
        operation_id: "op_1",
        status: "committed",
        changed_chapter_ids: ["art_1"],
        summary_state: "stale",
        ledger_suggestions_state: "not_started",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const operation = await fetchOperation("prj_1", "op_1");
    expect(operation.summary_state).toBe("stale");
    expect(operation.ledger_suggestions_state).toBe("not_started");
  });

  it("状态文案区分「缺失 / 待更新 / 一致」与建议三态", () => {
    expect(summaryLabel("missing")).toContain("没有");
    expect(summaryLabel("stale")).toContain("待更新");
    expect(summaryLabel("fresh")).toContain("一致");
    expect(suggestionsLabel("not_started")).toBe("尚未整理账本建议");
    expect(suggestionsLabel("available")).toBe("有账本建议可查看");
    // 已处理：不再提示可查看，也不冒充「尚未整理」。
    expect(suggestionsLabel("handled")).toBe("账本建议已处理");
  });
});
