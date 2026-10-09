/** CorpusPage 纯函数剧本（P7 T7，D6）：字节文案与六桶定序。 */

import { describe, expect, it } from "vitest";
import { bucketsOf, formatBytes } from "./CorpusPage";

describe("formatBytes", () => {
  it("B / KB / MB 三段进位（KB 两位内一位小数）", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
  });
});

describe("bucketsOf", () => {
  it("固定六桶顺序，缺失桶补 0；undefined 全零", () => {
    expect(bucketsOf({ "11-20": 3 })).toEqual([
      { label: "1-10", count: 0 },
      { label: "11-20", count: 3 },
      { label: "21-30", count: 0 },
      { label: "31-40", count: 0 },
      { label: "41-50", count: 0 },
      { label: "51+", count: 0 },
    ]);
    expect(bucketsOf(undefined)).toHaveLength(6);
    expect(bucketsOf(undefined).every((b) => b.count === 0)).toBe(true);
  });
});
