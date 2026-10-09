/**
 * Splitter 决策逻辑纯函数剧本（P6 T6，D6）：钳制边界 / 拖拽方向 / 读档解析。
 * 项目测试基建为 node 环境纯函数剧本（无 jsdom），DOM 拖拽接线走浏览器人工验收。
 */

import { describe, expect, it } from "vitest";
import { SPLIT_MAX, SPLIT_MIN, clampWidth, dragWidth, parseStoredWidth } from "./Splitter";

describe("clampWidth", () => {
  it("区间内原样返回", () => {
    expect(clampWidth(300)).toBe(300);
    expect(clampWidth(SPLIT_MIN)).toBe(SPLIT_MIN);
    expect(clampWidth(SPLIT_MAX)).toBe(SPLIT_MAX);
  });

  it("低于下限钳到 min（220）", () => {
    expect(clampWidth(0)).toBe(SPLIT_MIN);
    expect(clampWidth(219)).toBe(SPLIT_MIN);
  });

  it("高于上限钳到 max（480）", () => {
    expect(clampWidth(1000)).toBe(SPLIT_MAX);
    expect(clampWidth(481)).toBe(SPLIT_MAX);
  });

  it("非数值（NaN/Infinity）回落下限", () => {
    expect(clampWidth(Number.NaN)).toBe(SPLIT_MIN);
    expect(clampWidth(Number.POSITIVE_INFINITY)).toBe(SPLIT_MAX);
    expect(clampWidth(Number.NEGATIVE_INFINITY)).toBe(SPLIT_MIN);
  });

  it("自定义 min/max 生效（拖拽与读档共用同一条边界规则）", () => {
    expect(clampWidth(100, 150, 400)).toBe(150);
    expect(clampWidth(500, 150, 400)).toBe(400);
    expect(clampWidth(200, 150, 400)).toBe(200);
  });
});

describe("dragWidth", () => {
  it("左侧栏：向右拖（正 delta）变宽，向左拖变窄", () => {
    expect(dragWidth(300, 50, "left")).toBe(350);
    expect(dragWidth(300, -80, "left")).toBe(220);
  });

  it("右侧栏：向右拖（正 delta）变窄，向左拖变宽（方向相反）", () => {
    expect(dragWidth(340, 50, "right")).toBe(290);
    expect(dragWidth(340, -100, "right")).toBe(440);
  });

  it("零位移原样返回", () => {
    expect(dragWidth(300, 0, "left")).toBe(300);
    expect(dragWidth(300, 0, "right")).toBe(300);
  });
});

describe("parseStoredWidth", () => {
  it("null（无存档）回落默认宽", () => {
    expect(parseStoredWidth(null, 300)).toBe(300);
    expect(parseStoredWidth(null, 340)).toBe(340);
  });

  it("合法数值且在区间内：原样采用", () => {
    expect(parseStoredWidth("420", 300)).toBe(420);
  });

  it("越界存档钳制到默认边界（localStorage 可被手改，不信任）", () => {
    expect(parseStoredWidth("9999", 300)).toBe(SPLIT_MAX);
    expect(parseStoredWidth("-5", 300)).toBe(SPLIT_MIN);
  });

  it("损坏存档（非数值）回落默认宽", () => {
    expect(parseStoredWidth("", 300)).toBe(300);
    expect(parseStoredWidth("abc", 300)).toBe(300);
    expect(parseStoredWidth("null", 300)).toBe(300);
  });

  it("自定义 min/max 与钳制同规则", () => {
    expect(parseStoredWidth("80", 300, 150, 400)).toBe(150);
    expect(parseStoredWidth("999", 300, 150, 400)).toBe(400);
  });
});
