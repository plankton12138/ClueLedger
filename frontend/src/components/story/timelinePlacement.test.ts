import { describe, expect, it } from "vitest";

import { timeLabel, timePlacement } from "./timelinePlacement";

describe("time placement", () => {
  it("maps a range to the percentage of the domain", () => {
    const placement = timePlacement({ start_minute: 0, end_minute: 10 }, { min: -10, max: 10 });
    expect(placement).toEqual({ kind: "range", left: 50, width: 50 });
  });

  it("keeps an instant as a point instead of inventing a duration", () => {
    expect(timePlacement({ start_minute: -10, end_minute: -10 }, { min: -10, max: 10 })).toEqual({
      kind: "point",
      left: 0,
    });
  });

  it("has no position without a numeric domain", () => {
    expect(timePlacement(null, null)).toEqual({ kind: "unplaced" });
    expect(timePlacement({ start_minute: 1, end_minute: 2 }, null)).toEqual({ kind: "unplaced" });
  });

  it("rejects projections outside the domain instead of clamping", () => {
    expect(() =>
      timePlacement({ start_minute: 30, end_minute: 40 }, { min: -10, max: 10 }),
    ).toThrow("时间投影与坐标范围不一致");
    expect(() =>
      timePlacement({ start_minute: 5, end_minute: 4 }, { min: -10, max: 10 }),
    ).toThrow("时间投影与坐标范围不一致");
  });
});

describe("time label", () => {
  it("describes offsets relative to the origin", () => {
    expect(timeLabel(-10, -10, false, "案发当天 00:00")).toBe("基准前 10 分钟（案发当天 00:00）");
    expect(timeLabel(10, 20, false, "")).toBe("基准后 10 分钟 至 基准后 20 分钟");
    expect(timeLabel(0, 0, true, "")).toBe("约 基准时刻");
  });

  it("does not invent a time for unplaced nodes", () => {
    expect(timeLabel(null, null, false, "案发当天 00:00")).toBe("时间未定");
  });
});
