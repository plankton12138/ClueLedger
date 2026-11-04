/**
 * 修订模式的段落差异：段落对齐、相似段落配对、段内字符级增删。
 */

import { describe, expect, it } from "vitest";

import { changeCount, diffParagraphs, inlineDiff, splitParagraphs } from "./proseDiff";

describe("splitParagraphs", () => {
  it("按空行切段并忽略空白段", () => {
    expect(splitParagraphs("第一段\n\n第二段\n \n第三段\n")).toEqual(["第一段", "第二段", "第三段"]);
  });
});

describe("diffParagraphs", () => {
  it("改写的段落给出段内增删，而不是整行替换", () => {
    const before = "林岚很紧张，她害怕许砚继续追问。";
    const after = "林岚把杯沿转了半圈，还是怕许砚继续追问。";
    const blocks = diffParagraphs(before, after);

    expect(blocks).toHaveLength(1);
    const block = blocks[0];
    expect(block.kind).toBe("replace");
    if (block.kind !== "replace") return;
    const kept = block.inline.filter(span => span.kind === "equal").map(span => span.text).join("");
    const added = block.inline.filter(span => span.kind === "insert").map(span => span.text).join("");
    const removed = block.inline.filter(span => span.kind === "delete").map(span => span.text).join("");
    expect(kept).toContain("林岚");
    expect(removed).toContain("紧张");
    expect(added).toContain("杯沿");
  });

  it("未改动的段落保持原样，新增与删除分开显示", () => {
    const before = "第一段。\n\n第二段。";
    const after = "第一段。\n\n新增的一段。\n\n第二段。\n\n又一段。";
    const blocks = diffParagraphs(before, after);

    expect(blocks.map(block => block.kind)).toEqual([
      "equal",
      "insert",
      "equal",
      "insert",
    ]);
    expect(changeCount(blocks)).toBe(2);
  });

  it("完全不像的段落拆成删除与新增", () => {
    const blocks = diffParagraphs("甲甲甲。", "乙乙乙。");
    expect(blocks.map(block => block.kind)).toEqual(["delete", "insert"]);
  });

  it("相同文本没有改动", () => {
    const text = "第一段。\n\n第二段。";
    expect(diffParagraphs(text, text).every(block => block.kind === "equal")).toBe(true);
    expect(changeCount(diffParagraphs(text, text))).toBe(0);
  });
});

describe("inlineDiff", () => {
  it("删除与新增片段拼回原文与改后文", () => {
    const before = "窗外的雨没有停。";
    const after = "窗外的雨小了些，檐口还在滴水。";
    const spans = inlineDiff(before, after);

    expect(spans.filter(span => span.kind !== "insert").map(span => span.text).join("")).toBe(before);
    expect(spans.filter(span => span.kind !== "delete").map(span => span.text).join("")).toBe(after);
  });

  it("过长的段落退化为整段改写", () => {
    const before = "甲".repeat(4001);
    const after = "乙".repeat(4001);
    expect(inlineDiff(before, after).map(span => span.kind)).toEqual(["delete", "insert"]);
  });
});
