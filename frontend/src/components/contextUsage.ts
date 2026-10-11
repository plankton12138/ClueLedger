/**
 * 上下文占用展示（P10 T12，D11）：token 数人类可读化 + 占用率文案。
 *
 * 纯函数模块（同 options.ts / confirm.ts 惯例）——组件只负责取数与排版，
 * 换算逻辑单测覆盖，避免依赖 jsdom 的组件测试。
 */

import type { TokenUsagePayload } from "../api/types";

/** token 数 → 紧凑可读：<1000 原样；≥1000 用 k（<100k 保留一位小数，越大取整）。 */
export function formatTokens(tokens: number): string {
  const n = Math.max(0, Math.round(tokens));
  if (n < 1000) return String(n);
  const k = n / 1000;
  return k >= 100 ? `${Math.round(k)}k` : `${Math.round(k * 10) / 10}k`;
}

/** 「上下文 X / 窗口 Y（Z%）」；窗口未知（0）时占用率记 0，不除零。 */
export function formatContextUsage(usage: TokenUsagePayload): string {
  const raw = usage.window_tokens > 0 ? (usage.context_tokens / usage.window_tokens) * 100 : 0;
  const pct = Math.max(0, Math.min(100, Math.round(raw)));
  return `上下文 ${formatTokens(usage.context_tokens)} / 窗口 ${formatTokens(usage.window_tokens)}（${pct}%）`;
}