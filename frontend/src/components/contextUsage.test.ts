/**
 * 上下文占用展示纯函数测试（P10 T12，D11）：token 紧凑化与占用率文案。
 */

import { describe, expect, it } from "vitest";
import type { TokenUsagePayload } from "../api/types";
import { formatContextUsage, formatTokens } from "./contextUsage";

function usage(partial: Partial<TokenUsagePayload>): TokenUsagePayload {
  return {
    input: 0,
    output: 0,
    total: 0,
    cache_read: 0,
    cache_write: 0,
    context_tokens: 0,
    window_tokens: 0,
    source: "chars",
    ...partial,
  };
}

describe("formatTokens", () => {
  it("<1000 原样，负数归零", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(-50)).toBe("0");
  });

  it("≥1000 用 k；<100k 保留一位小数，≥100k 取整", () => {
    expect(formatTokens(1000)).toBe("1k");
    expect(formatTokens(1500)).toBe("1.5k");
    expect(formatTokens(12_800)).toBe("12.8k");
    expect(formatTokens(128_000)).toBe("128k");
  });
});

describe("formatContextUsage", () => {
  it("标准占用率：上下文/窗口/百分比", () => {
    expect(formatContextUsage(usage({ context_tokens: 12_800, window_tokens: 128_000 }))).toBe(
      "上下文 12.8k / 窗口 128k（10%）",
    );
  });

  it("窗口为 0（未配置）不除零，占用率记 0", () => {
    expect(formatContextUsage(usage({ context_tokens: 500, window_tokens: 0 }))).toBe(
      "上下文 500 / 窗口 0（0%）",
    );
  });

  it("占用超窗口时百分比封顶 100，不显示夸张值", () => {
    expect(formatContextUsage(usage({ context_tokens: 300_000, window_tokens: 128_000 }))).toBe(
      "上下文 300k / 窗口 128k（100%）",
    );
  });
});