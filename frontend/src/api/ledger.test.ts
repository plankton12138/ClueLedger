/**
 * 设定集读取客户端契约（V03-10 M3）：只保留正式条目读取。
 *
 * 写方法（创建/修订/候选确认）随对话成为唯一写通道一并移除，前端不再直接写。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchLedger } from "./ledger";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ledger client", () => {
  it("fetchLedger 返回可引用条目", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          entries: [
            {
              id: "chr_1",
              kind: "story.character",
              kind_label: "人物",
              title: "林昭",
              summary: "侦探",
            },
          ],
        }),
      ),
    );
    const entries = await fetchLedger("p1");
    expect(entries).toHaveLength(1);
    expect(entries[0].kind_label).toBe("人物");
  });

  it("请求打到项目的 ledger 读取端点", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return jsonResponse({ entries: [] });
      }),
    );
    await fetchLedger("p1");
    expect(calls[0]).toContain("/projects/p1/ledger");
  });
});
