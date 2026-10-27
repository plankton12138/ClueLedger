/**
 * 会话用量视图的展示判定（0.9 U0）。
 *
 * 这里不测聚合数字，只钉「什么状态显示什么」：零、统计中、未知、部分、完整
 * 五种状态各自有独立文案，不能靠数字是否为 0 来推断。
 */

import { describe, expect, it, vi } from "vitest";

import type { ContextView, UsageTotals } from "./contextUsage";
import {
  breakdown,
  contextLabel,
  formatTokens,
  usageDisplayState,
  usageLabel,
} from "./contextUsage";

function totals(overrides: Partial<UsageTotals> = {}): UsageTotals {
  return {
    total_tokens: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    cache_write_tokens: null,
    finished_requests: 0,
    requests_without_usage: 0,
    requests_in_progress: 0,
    model_duration_ms: null,
    ...overrides,
  };
}

describe("usageDisplayState", () => {
  it("没有任何请求时是零，不是未知", () => {
    expect(usageDisplayState(totals())).toBe("empty");
  });

  it("只有进行中的请求时是统计中", () => {
    expect(usageDisplayState(totals({ requests_in_progress: 1 }))).toBe("pending");
  });

  it("有请求但全部没有用量时是未知", () => {
    expect(
      usageDisplayState(totals({ finished_requests: 2, requests_without_usage: 2 })),
    ).toBe("unknown");
  });

  it("已知累计中夹着缺失请求时是部分", () => {
    expect(
      usageDisplayState(
        totals({ total_tokens: 4200, finished_requests: 3, requests_without_usage: 1 }),
      ),
    ).toBe("partial");
  });

  it("全部请求都有用量时是完整", () => {
    expect(
      usageDisplayState(totals({ total_tokens: 4200, finished_requests: 3 })),
    ).toBe("known");
  });
});

describe("fetchConversationUsage", () => {
  it("按作品与会话读取，字段原样交给界面", async () => {
    const body = {
      conversation_id: "c1",
      through_seq: 12,
      usage: {
        total_tokens: 4200,
        input_tokens: 4000,
        output_tokens: 200,
        cache_read_tokens: 3000,
        cache_write_tokens: 0,
        finished_requests: 3,
        requests_without_usage: 1,
        requests_in_progress: 0,
        model_duration_ms: 5300,
      },
      context: {
        status: "ready",
        estimated_active_tokens: 4200,
        last_request_input_tokens: 4000,
        context_window_tokens: 131_072,
        input_limit_tokens: 122_880,
        output_reserve_tokens: 4096,
        budget: "ok",
        instructions_tokens: 1200,
        tools_tokens: 900,
        conversation_tokens: 2100,
        method: "cjk-aware-v1",
        fingerprint: "f",
        limits_revision: "r",
      },
      diagnostics: [],
      revision: "r1",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { fetchConversationUsage } = await import("./workspace");
    const view = await fetchConversationUsage("w1", "c1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/file-workspaces/w1/conversations/c1/usage",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(view.through_seq).toBe(12);
    expect(contextLabel(view.context)).toBe("上下文 3%");
    expect(usageDisplayState(view.usage)).toBe("partial");
    vi.unstubAllGlobals();
  });
});

describe("用量文案", () => {
  const context = (overrides: Partial<ContextView> = {}): ContextView => ({
    status: "ready",
    estimated_active_tokens: 12_800,
    last_request_input_tokens: 12_000,
    context_window_tokens: 131_072,
    input_limit_tokens: 122_880,
    output_reserve_tokens: 4096,
    budget: "ok",
    instructions_tokens: 4000,
    tools_tokens: 2500,
    conversation_tokens: 6300,
    method: "cjk-aware-v1",
    fingerprint: "f",
    limits_revision: "r",
    ...overrides,
  });

  it("一万以下给原数，以上用万", () => {
    expect(formatTokens(8420)).toBe("8420");
    expect(formatTokens(12_800)).toBe("1.3 万");
    expect(formatTokens(null)).toBe("—");
  });

  it("窗口未知时只给 token 数，不给百分比", () => {
    const label = contextLabel(context({ context_window_tokens: null }));

    expect(label).toBe("上下文 ≈1.3 万");
  });

  it("有窗口时给出占比", () => {
    expect(contextLabel(context())).toBe("上下文 10%");
  });

  it("首次请求前是待计算", () => {
    expect(contextLabel(context({ status: "pending", estimated_active_tokens: null }))).toBe(
      "上下文 待计算",
    );
    expect(contextLabel(null)).toBe("上下文 待计算");
  });

  it("累计区分零、未知、部分与完整", () => {
    const base: UsageTotals = {
      total_tokens: null,
      input_tokens: null,
      output_tokens: null,
      cache_read_tokens: null,
      cache_write_tokens: null,
      finished_requests: 0,
      requests_without_usage: 0,
      requests_in_progress: 0,
      model_duration_ms: null,
    };

    expect(usageLabel(base)).toBe("累计 0");
    expect(usageLabel({ ...base, requests_in_progress: 1 })).toBe("累计 统计中");
    expect(usageLabel({ ...base, finished_requests: 2, requests_without_usage: 2 })).toBe(
      "累计 未知",
    );
    expect(
      usageLabel({ ...base, total_tokens: 42_000, finished_requests: 3, requests_without_usage: 1 }),
    ).toBe("累计 4.2 万（部分未统计）");
    expect(usageLabel({ ...base, total_tokens: 42_000, finished_requests: 3 })).toBe(
      "累计 4.2 万",
    );
  });

  it("构成固定三项，缺值留空", () => {
    const items = breakdown(context({ tools_tokens: null }));

    expect(items.map((item) => item.label)).toEqual(["约定与资料", "工具定义", "对话与结果"]);
    expect(items[1].tokens).toBeNull();
  });
});
