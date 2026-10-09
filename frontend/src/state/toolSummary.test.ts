/**
 * 工具卡片摘要剧本（P7 T3，计划 D4）：details 摊平 / 单元格口径 / 泄压阀三情形
 * （无 details → 空数组不渲染该区；空对象 → 空数组；长值截断不撑爆卡片）。
 */

import { describe, expect, it } from "vitest";
import { toolDetailLines } from "./toolSummary";

describe("toolDetailLines", () => {
  it("顶层 record 摊平为逐字段行", () => {
    expect(toolDetailLines({ chapter: "ch03", chars: 2348, ok: true })).toEqual([
      { label: "chapter", value: "ch03" },
      { label: "chars", value: "2348" },
      { label: "ok", value: "是" },
    ]);
  });

  it("空值/空数组/缺失字段都是「—」（合法空结果，不装作有内容）", () => {
    expect(toolDetailLines({ a: null, b: "", c: [], d: undefined })).toEqual([
      { label: "a", value: "—" },
      { label: "b", value: "—" },
      { label: "c", value: "—" },
      { label: "d", value: "—" },
    ]);
  });

  it("数组顿号连接并递归单元格口径；嵌套对象紧凑 JSON", () => {
    expect(toolDetailLines({ names: ["雾夜", "白川"], meta: { k: 1 } })).toEqual([
      { label: "names", value: "雾夜、白川" },
      { label: "meta", value: '{"k":1}' },
    ]);
  });

  it("超长值截断到 80 字符加省略号（卡片不撑爆）", () => {
    const long = "字".repeat(120);
    const [line] = toolDetailLines({ text: long });
    expect(line?.value).toBe(`${"字".repeat(80)}…`);
  });

  it("null/undefined → 空数组（无 details 区可渲染）", () => {
    expect(toolDetailLines(null)).toEqual([]);
    expect(toolDetailLines(undefined)).toEqual([]);
  });

  it("空对象 → 空数组（无字段 ≠ 出错）", () => {
    expect(toolDetailLines({})).toEqual([]);
  });

  it("顶层非 record 包成单行「结果」", () => {
    expect(toolDetailLines("已写入 ch03")).toEqual([{ label: "结果", value: "已写入 ch03" }]);
    expect(toolDetailLines(42)).toEqual([{ label: "结果", value: "42" }]);
    expect(toolDetailLines(["a", "b"])).toEqual([{ label: "结果", value: "a、b" }]);
  });
});
