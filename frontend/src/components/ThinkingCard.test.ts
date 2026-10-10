/**
 * ThinkingCard 纯函数剧本（P9 T2，D1）：字数口径 / 折叠文案 / 流式文案。
 * 项目测试基建为 node 环境纯函数剧本（无 jsdom），折叠交互走浏览器人工验收。
 */

import { describe, expect, it } from "vitest";
import {
  thinkingCharCount,
  thinkingCollapsedLabel,
  thinkingStreamingLabel,
} from "./ThinkingCard";

describe("thinkingCharCount", () => {
  it("统计非空白字符", () => {
    expect(thinkingCharCount("凶手就在现场")).toBe(6);
    expect(thinkingCharCount("A 与 B\n 是\t凶手")).toBe(6);
  });

  it("空串与纯空白为 0", () => {
    expect(thinkingCharCount("")).toBe(0);
    expect(thinkingCharCount(" \n\t ")).toBe(0);
  });
});

describe("thinkingCollapsedLabel", () => {
  it("含字数与展开提示", () => {
    expect(thinkingCollapsedLabel(128)).toBe("◈ 思路 · 128 字 · 展开");
  });
});

describe("thinkingStreamingLabel", () => {
  it("零字数只显示思考中", () => {
    expect(thinkingStreamingLabel(0)).toBe("思考中…");
  });

  it("有字数时随流增长显示", () => {
    expect(thinkingStreamingLabel(42)).toBe("思考中… 42 字");
  });
});
