/**
 * 章节文本纯函数测试（P5 T1）：解析规约与 write_chapter 落盘格式一致——
 * 首行 `# 第N章 标题`，空行分段。呈现侧不改写文本，只做读取。
 */

import { describe, expect, it } from "vitest";
import { countChars, parseChapterHeading, splitParagraphs } from "./chapter";

describe("parseChapterHeading", () => {
  it("首行 `# 第3章 雪夜来客` → 标题 + 其余为正文", () => {
    const parsed = parseChapterHeading("# 第3章 雪夜来客\n\n雪下得比傍晚时更大了。\n");
    expect(parsed.title).toBe("雪夜来客");
    expect(parsed.body).toBe("\n雪下得比傍晚时更大了。\n");
  });

  it("无标题后缀 → title 为 null（呈现回退到章节号）", () => {
    expect(parseChapterHeading("# 第3章\n\n正文").title).toBeNull();
  });

  it("首行不是标题 → 整体视为正文", () => {
    const parsed = parseChapterHeading("雪下得很大。\n\n又停了。");
    expect(parsed.title).toBeNull();
    expect(parsed.body).toBe("雪下得很大。\n\n又停了。");
  });

  it("标题行允许全角数字与多字章号（解析只认 `第…章` 形状）", () => {
    expect(parseChapterHeading("# 第12章 封锁的雪").title).toBe("封锁的雪");
  });
});

describe("splitParagraphs（空行分段，与 T2 服务端 diff 同规约）", () => {
  it("空行分隔多段", () => {
    expect(splitParagraphs("第一段。\n\n第二段。\n\n第三段。")).toEqual([
      "第一段。",
      "第二段。",
      "第三段。",
    ]);
  });

  it("段内单换行并入同段；首尾空段丢弃", () => {
    expect(splitParagraphs("\n\n一行\n两行。\n\n")).toEqual(["一行两行。"]);
  });

  it("全空白 → 空数组", () => {
    expect(splitParagraphs("\n  \n\n")).toEqual([]);
  });
});

describe("countChars", () => {
  it("非空白字符计数（CJK 直觉字数）", () => {
    expect(countChars("雪下得很大，\n 又停了。")).toBe(10);
  });
  it("空文本 → 0", () => {
    expect(countChars("")).toBe(0);
  });
});
