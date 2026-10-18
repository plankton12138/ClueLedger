/**
 * 账本客户端契约（V03-3.2）：表单描述同源、保存幂等键、修订带回 CAS 版本。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createLedgerEntry,
  fetchLedger,
  fetchLedgerEntry,
  fetchLedgerSchema,
  updateLedgerEntry,
} from "./ledger";
import { V2Error } from "./v2";

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
            { id: "chr_1", kind: "story.character", kind_label: "人物", title: "林昭", summary: "侦探" },
          ],
        }),
      ),
    );
    const entries = await fetchLedger("p1");
    expect(entries).toHaveLength(1);
    expect(entries[0].kind_label).toBe("人物");
  });

  it("fetchLedgerSchema 返回字段描述", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          kinds: [
            {
              kind: "story.character",
              label: "人物",
              fields: [{ key: "name", label: "姓名", type: "text", required: true }],
            },
          ],
        }),
      ),
    );
    const kinds = await fetchLedgerSchema("p1");
    expect(kinds[0].fields[0].key).toBe("name");
  });

  it("创建条目带 request_id，修订使用 PATCH 并带 expected_revision", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({
        status: "committed",
        commit_id: "cmt_1",
        entry: {
          id: "chr_1",
          kind: "story.character",
          kind_label: "人物",
          title: "姐姐",
          summary: "",
          revision_id: "rev_2",
          content: { name: "姐姐" },
        },
        replayed: false,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await createLedgerEntry("p1", {
      kind: "story.character",
      fields: { name: "姐姐" },
      request_id: "req-1",
    });
    expect(calls[0].url).toContain("/projects/p1/ledger/entries");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      kind: "story.character",
      fields: { name: "姐姐" },
      request_id: "req-1",
    });

    await updateLedgerEntry("p1", "chr_1", {
      fields: { goal: "守住旅馆" },
      expected_revision: "rev_2",
      request_id: "req-2",
    });
    expect(calls[1].url).toContain("/ledger/entries/chr_1");
    expect(calls[1].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      fields: { goal: "守住旅馆" },
      expected_revision: "rev_2",
      request_id: "req-2",
    });
  });

  it("详情读取与 revision 冲突错误码原样透出", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: "LEDGER_ENTRY_CONFLICT",
              category: "violation",
              where: "ReviseLedgerEntry.expected_revision",
              why: "版本已过期",
              fix: "重新读取后再保存",
              details: {},
            },
          },
          409,
        ),
      ),
    );
    await expect(fetchLedgerEntry("p1", "chr_1")).rejects.toBeInstanceOf(V2Error);
    await expect(fetchLedgerEntry("p1", "chr_1")).rejects.toMatchObject({
      code: "LEDGER_ENTRY_CONFLICT",
    });
  });
});
