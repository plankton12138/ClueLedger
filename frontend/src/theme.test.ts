import { describe, expect, it } from "vitest";
import { theme } from "./theme";
describe("iOS 6 浅色主题", () => {
  it("蓝色金属界面与独立正文纸面", () => {
    expect(theme.colors.page).toBe("#E7EAEB");
    expect(theme.colors.sheet).toBe("#F7F3E8");
    expect(theme.colors.fg).not.toBe(theme.colors.page);
    expect(theme.colors.chromeLow).not.toBe(theme.colors.chromeHigh);
  });
  it("状态与差异有独立语义", () => {
    expect(
      new Set([
        theme.colors.green,
        theme.colors.amber,
        theme.colors.red,
        theme.colors.blue,
      ]).size,
    ).toBe(4);
    expect(theme.colors.delBg).not.toBe(theme.colors.insBg);
    expect(theme.fonts.serif).toContain('"Noto Serif SC"');
  });
});
