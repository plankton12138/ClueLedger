/**
 * 段内字符级高亮纯函数剧本（P5 T5，D3/D7）：「两段文本进、四段切片出」。
 * 服务端只给整段新旧文本，变更中段的定位由前端前后缀修剪完成。
 */

import { describe, expect, it } from "vitest";
import { inlineDiff } from "./diff";

describe("inlineDiff", () => {
  it("中文句中替换：前缀/后缀不变，中段分别为新旧文本", () => {
    const d = inlineDiff("下得比傍晚时更大了", "下得比午夜时更大了");
    expect(d.prefix).toBe("下得比");
    expect(d.oldMiddle).toBe("傍晚");
    expect(d.newMiddle).toBe("午夜");
    expect(d.suffix).toBe("时更大了");
  });

  it("纯追加：旧中段为空，新中段是追加内容", () => {
    const d = inlineDiff("雪下得很大", "雪下得很大，风也起了");
    expect(d.prefix).toBe("雪下得很大");
    expect(d.oldMiddle).toBe("");
    expect(d.newMiddle).toBe("，风也起了");
    expect(d.suffix).toBe("");
  });

  it("纯删除：新中段为空，旧中段是删除内容", () => {
    const d = inlineDiff("九点四十七分整", "九点四十七");
    expect(d.prefix).toBe("九点四十七");
    expect(d.oldMiddle).toBe("分整");
    expect(d.newMiddle).toBe("");
    expect(d.suffix).toBe("");
  });

  it("完全相同：中段均为空、整串落入前缀（无高亮）", () => {
    const d = inlineDiff("原样不动", "原样不动");
    expect(d.prefix).toBe("原样不动");
    expect(d.oldMiddle).toBe("");
    expect(d.newMiddle).toBe("");
    expect(d.suffix).toBe("");
  });

  it("前后缀不重叠（前缀吃满后，后缀只在其后计算）", () => {
    const d = inlineDiff("abXab", "abYab");
    expect(d.prefix).toBe("ab");
    expect(d.oldMiddle).toBe("X");
    expect(d.newMiddle).toBe("Y");
    expect(d.suffix).toBe("ab");
  });

  it("整段替换：无公共前后缀，整串进中段", () => {
    const d = inlineDiff("旧的整段", "新的整段文字");
    expect(d.prefix).toBe("");
    expect(d.oldMiddle).toBe("旧的整段");
    expect(d.newMiddle).toBe("新的整段文字");
    expect(d.suffix).toBe("");
  });
});