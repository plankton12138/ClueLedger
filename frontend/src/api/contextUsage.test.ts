/**
 * 会话用量视图的展示判定（0.9 U0）。
 *
 * 这里不测聚合数字，只钉「什么状态显示什么」：零、统计中、未知、部分、完整
 * 五种状态各自有独立文案，不能靠数字是否为 0 来推断。
 */

import { describe, expect, it } from "vitest";

import type { UsageTotals } from "./contextUsage";
import { usageDisplayState } from "./contextUsage";

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
