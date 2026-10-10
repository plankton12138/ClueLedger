/**
 * stageSuggestions 纯函数剧本（P9 T3，D5）：stage 分组/建议映射 +
 * 等待占位条件。DOM 渲染走浏览器人工验收。
 */

import { describe, expect, it } from "vitest";
import {
  shouldShowThinkingPlaceholder,
  stageGroup,
  stageSuggestions,
} from "./stageSuggestions";

describe("stageGroup", () => {
  it("intake 及未知阶段归 intake", () => {
    expect(stageGroup("intake")).toBe("intake");
    expect(stageGroup("")).toBe("intake");
    expect(stageGroup("nonsense")).toBe("intake");
  });

  it("bible_drafting/bible_confirmed 归 bible", () => {
    expect(stageGroup("bible_drafting")).toBe("bible");
    expect(stageGroup("bible_confirmed")).toBe("bible");
  });

  it("writing/revising 归 writing", () => {
    expect(stageGroup("writing")).toBe("writing");
    expect(stageGroup("revising")).toBe("writing");
  });
});

describe("stageSuggestions", () => {
  it("intake：投递构想 / 聊能力", () => {
    const suggestions = stageSuggestions("intake");
    expect(suggestions).toHaveLength(2);
    expect(suggestions[0].label).toBe("我要投递一个故事构想");
    expect(suggestions[1].label).toBe("先聊聊你能做什么");
  });

  it("bible 阶段：继续建账 / 修改设定", () => {
    const suggestions = stageSuggestions("bible_confirmed");
    expect(suggestions[0].label).toBe("按现有构想继续建账");
    expect(suggestions[1].label).toBe("我要修改设定");
  });

  it("writing 阶段：写下一章 / 检查一致性", () => {
    const suggestions = stageSuggestions("revising");
    expect(suggestions[0].label).toBe("写下一章");
    expect(suggestions[1].label).toBe("检查一致性");
  });

  it("每条建议 label 与 prompt 都非空", () => {
    for (const stage of ["intake", "bible_drafting", "writing"]) {
      for (const suggestion of stageSuggestions(stage)) {
        expect(suggestion.label.length).toBeGreaterThan(0);
        expect(suggestion.prompt.length).toBeGreaterThan(0);
      }
    }
  });
});

describe("shouldShowThinkingPlaceholder", () => {
  const base = {
    running: true,
    assistantBuffer: "",
    thinkingBuffer: "",
    hasRunningTool: false,
  };

  it("running 且全无输出 → true", () => {
    expect(shouldShowThinkingPlaceholder(base)).toBe(true);
  });

  it("非 running → false", () => {
    expect(shouldShowThinkingPlaceholder({ ...base, running: false })).toBe(false);
  });

  it("有正文/思考 buffer → false", () => {
    expect(shouldShowThinkingPlaceholder({ ...base, assistantBuffer: "x" })).toBe(false);
    expect(shouldShowThinkingPlaceholder({ ...base, thinkingBuffer: "x" })).toBe(false);
  });

  it("有运行中工具卡 → false（工具卡自己显示执行中）", () => {
    expect(shouldShowThinkingPlaceholder({ ...base, hasRunningTool: true })).toBe(false);
  });
});
