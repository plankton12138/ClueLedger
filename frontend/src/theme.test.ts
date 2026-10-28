import { describe, expect, it } from "vitest";
import { theme } from "./theme";

function luminance(hex: string): number {
  const rgb = hex.slice(1).match(/../g)!.map(part => parseInt(part, 16) / 255)
    .map(n => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4);
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
}
function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
describe("经典 OS X Aqua 浅色主题", () => {
  it("基础控件的文字、状态和焦点满足对比目标", () => {
    const c = theme.colors.ui;
    for (const [fg, bg] of [[c.text, c.surface], [c.secondaryText, c.surfaceLow], [c.text, c.paper],
      [c.onAccent, c.accent], [c.onAccent, c.accentHover], [c.onSelected, c.selected],
      [c.danger, c.dangerSurface], [c.warning, c.warningSurface], [c.success, c.successSurface], [c.disabledText, c.disabledSurface]]) {
      expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(c.focus, c.surface)).toBeGreaterThanOrEqual(3);
    expect(contrast(c.edge, c.surface)).toBeGreaterThanOrEqual(3);
  });
  it("H1选中行与金色按钮在渐变最弱端达到普通文字对比", () => {
    const c = theme.colors;
    for (const background of [c.selectedHigh, c.selectedLow]) expect(contrast(c.onSelected, background)).toBeGreaterThanOrEqual(4.5);
    for (const background of [c.btnGoldHigh, c.btnGoldMid, c.btnGoldLow, c.btnGoldHoverHigh, c.btnGoldHoverLow]) expect(contrast(c.btnGoldText, background)).toBeGreaterThanOrEqual(4.5);
    for (const foreground of [c.fg, c.sub]) expect(contrast(foreground, c.selHigh)).toBeGreaterThanOrEqual(4.5);
  });
  it("银灰界面与独立正文纸面", () => {
    expect(theme.colors.page).toBe(theme.colors.ui.canvas);
    expect(theme.colors.sheet).toBe(theme.colors.ui.paper);
    expect(theme.colors.fg).not.toBe(theme.colors.page);
    expect(theme.colors.chromeLow).not.toBe(theme.colors.chromeHigh);
  });
  it("公共页面的状态文字与弱提示达到普通文字对比", () => {
    const c = theme.colors;
    for (const foreground of [c.green, c.amber, c.red, c.blue, c.mute, c.faint]) {
      for (const background of [c.ui.surface, c.ui.surfaceLow, c.ui.paper]) expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
    }
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
