/**
 * 会话选择器纯函数剧本（P10 T14，D13）：会话行次要信息文案。
 * 项目测试基建为 node 环境纯函数剧本（无 jsdom），Popover 交互走浏览器人工验收。
 */

import { describe, expect, it } from "vitest";
import type { SessionSummary } from "../api/rest";
import { sessionMetaLabel } from "./SessionSelector";

function session(partial: Partial<SessionSummary>): SessionSummary {
  return {
    id: "session-20260101-000000-abcd",
    title: "会话",
    created_at: "2026-01-01T00:00:00",
    last_active: "2026-01-01T00:00:00",
    message_count: 0,
    active: false,
    ...partial,
  };
}

describe("sessionMetaLabel", () => {
  it("拼接消息条数与最后活跃时间（T 换空格）", () => {
    expect(
      sessionMetaLabel(session({ message_count: 12, last_active: "2026-10-11T09:30:00" })),
    ).toBe("12 条 · 2026-10-11 09:30:00");
  });

  it("零消息也照常显示条数", () => {
    expect(sessionMetaLabel(session({ message_count: 0 }))).toBe(
      "0 条 · 2026-01-01 00:00:00",
    );
  });
});