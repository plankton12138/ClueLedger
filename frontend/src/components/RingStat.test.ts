/**
 * 共享视觉组件几何纯函数剧本（P7 T2，计划 D3/D4 测试要点）：
 * 环——钳制边界 / 百分比取整 / dasharray 与原 RecoveryRing size=36 逐值一致；
 * 直方图——比例布局 / 全零与空序列 / 非法计数兜底。渲染冒烟走浏览器人工验收
 * （项目测试基建为 node 环境纯函数剧本，无 jsdom）。
 */

import { describe, expect, it } from "vitest";
import { clampRate, ringDash, ringPct } from "./RingStat";
import { HISTOGRAM_LABEL_BAND, layoutHistogram } from "./Histogram";

describe("clampRate / ringPct", () => {
  it("区间内原样，越界钳到 [0,1]，NaN 视为 0", () => {
    expect(clampRate(0.42)).toBe(0.42);
    expect(clampRate(-0.1)).toBe(0);
    expect(clampRate(1.1)).toBe(1);
    expect(clampRate(Number.NaN)).toBe(0);
  });

  it("百分比四舍五入且不越界（0.995 → 100%）", () => {
    expect(ringPct(0.42)).toBe(42);
    expect(ringPct(0.995)).toBe(100);
    expect(ringPct(0)).toBe(0);
  });
});

describe("ringDash（与原 RecoveryRing 逐值一致性）", () => {
  it("size=36（r=14）时 dasharray 与原实现完全相同", () => {
    // 原：circumference = 2π×14 ≈ 87.9646；rate=0.75 → 65.9734
    expect(ringDash(0.75, 14)).toBe("66.0 88.0");
    expect(ringDash(0, 14)).toBe("0.0 88.0");
    expect(ringDash(1, 14)).toBe("88.0 88.0");
  });

  it("rate 越界同样先钳制再算弧长", () => {
    expect(ringDash(2, 14)).toBe(ringDash(1, 14));
  });
});

describe("layoutHistogram", () => {
  it("最高柱占满绘图区，其余按比例；标签带预留常量生效", () => {
    const bars = layoutHistogram(
      [
        { label: "1-10", count: 10 },
        { label: "11-20", count: 5 },
      ],
      280,
      120,
    );
    const plotH = 120 - HISTOGRAM_LABEL_BAND - 4;
    expect(bars[1]?.h).toBeCloseTo(plotH / 2, 6);
    expect(bars[0]?.h).toBeCloseTo(plotH, 6);
    // 底边对齐：y + h 恒等于绘图区底
    for (const b of bars) {
      expect(b.y + b.h).toBeCloseTo(120 - HISTOGRAM_LABEL_BAND, 6);
    }
  });

  it("柱宽 = slot×0.6 居中，柱心 = slot 中心", () => {
    const bars = layoutHistogram(
      [
        { label: "a", count: 1 },
        { label: "b", count: 2 },
      ],
      200,
      100,
    );
    expect(bars[0]?.w).toBeCloseTo(60, 6);
    expect(bars[0]?.cx).toBeCloseTo(50, 6);
    expect(bars[1]?.cx).toBeCloseTo(150, 6);
  });

  it("全零计数：柱高全 0 但柱位仍在（空桶是分布信息）", () => {
    const bars = layoutHistogram(
      [
        { label: "a", count: 0 },
        { label: "b", count: 0 },
      ],
      200,
      100,
    );
    expect(bars).toHaveLength(2);
    for (const b of bars) {
      expect(b.h).toBe(0);
    }
  });

  it("空序列返回空数组（渲染层显示占位文案）", () => {
    expect(layoutHistogram([], 200, 100)).toEqual([]);
  });

  it("非法计数兜底：负数与 NaN 按 0 处理，不产生倒挂柱", () => {
    const bars = layoutHistogram(
      [
        { label: "a", count: -3 },
        { label: "b", count: Number.NaN },
        { label: "c", count: 4 },
      ],
      300,
      100,
    );
    expect(bars[0]?.count).toBe(0);
    expect(bars[1]?.count).toBe(0);
    expect(bars[0]?.h).toBe(0);
    expect(bars[1]?.h).toBe(0);
    expect(bars[2]?.h).toBeGreaterThan(0);
  });
});
