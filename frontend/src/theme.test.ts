import { describe, expect, it } from "vitest";
import { theme } from "./theme";

// 设计令牌是 T8/T9 全部样式的源头：把视觉稿的关键色值钉进测试，
// 防止后续调样式时无意识漂移（与后端"用测试钉住协议"同一哲学）。
describe("Digital Noir 设计令牌", () => {
  it("主色与文本色与视觉稿一致（submission/03-cover-design.md）", () => {
    expect(theme.colors.gold).toBe("#C8A84D"); // 暗金主色
    expect(theme.colors.goldSoft).toBe("#D4B896"); // 淡金
    expect(theme.colors.paper).toBe("#E0DCC8"); // 正文浅灰
  });

  it("语义状态色覆盖伏笔生命周期三态", () => {
    expect(theme.colors.planned).toMatch(/^#[0-9A-F]{6}$/i); // 已计划：蓝
    expect(theme.colors.planted).toMatch(/^#[0-9A-F]{6}$/i); // 已埋设：绿
    expect(theme.colors.overdue).toMatch(/^#[0-9A-F]{6}$/i); // 逾期：红
  });

  it("底色是深靛蓝系（亮度足够暗，保证暗色主题）", () => {
    const ink = theme.colors.ink;
    const r = parseInt(ink.slice(1, 3), 16);
    const g = parseInt(ink.slice(3, 5), 16);
    const b = parseInt(ink.slice(5, 7), 16);
    expect(b).toBeGreaterThan(r); // 蓝分量主导
    expect(Math.max(r, g, b)).toBeLessThan(0x40); // 足够深
  });

  it("中文标题走衬线字体栈", () => {
    expect(theme.fonts.serif[0]).toBe('"Noto Serif SC"');
  });
});
