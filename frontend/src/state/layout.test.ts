/**
 * 布局偏好状态（0.4 V04-11）。
 *
 * 重点在边界：存储缺失、损坏、越界都不能把界面排成畸形宽度；
 * 写入失败只丢偏好，不影响本次会话。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { CHAT, DEFAULT_LAYOUT, SIDEBAR, clampWidth, parseLayout, useLayoutStore } from "./layout";

describe("clampWidth", () => {
  it("取整并限制在区间内", () => {
    expect(clampWidth(261.6, SIDEBAR)).toBe(262);
    expect(clampWidth(10, SIDEBAR)).toBe(SIDEBAR.min);
    expect(clampWidth(9999, SIDEBAR)).toBe(SIDEBAR.max);
  });

  it("非数值按默认值处理", () => {
    expect(clampWidth(Number.NaN, SIDEBAR)).toBe(SIDEBAR.default);
    expect(clampWidth("320", SIDEBAR)).toBe(SIDEBAR.default);
    expect(clampWidth(undefined, SIDEBAR)).toBe(SIDEBAR.default);
  });
});

describe("parseLayout", () => {
  it("没有存储内容时用默认值", () => {
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
  });

  it("内容损坏或不是对象时回到默认值", () => {
    expect(parseLayout("{not json")).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout("null")).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout('"wide"')).toEqual(DEFAULT_LAYOUT);
  });

  it("越界宽度钳制，非布尔显隐回到默认开启", () => {
    const prefs = parseLayout(
      JSON.stringify({ sidebarWidth: 9999, chatWidth: "wide", chatVisible: "yes" }),
    );
    expect(prefs.sidebarWidth).toBe(SIDEBAR.max);
    expect(prefs.chatWidth).toBe(CHAT.default);
    expect(prefs.chatVisible).toBe(true);
  });

  it("保留合法的显隐状态", () => {
    expect(parseLayout(JSON.stringify({ chatVisible: false })).chatVisible).toBe(false);
  });
});

describe("useLayoutStore", () => {
  beforeEach(() => {
    useLayoutStore.setState({ ...DEFAULT_LAYOUT });
    vi.unstubAllGlobals();
  });

  it("改宽度会钳制并写回本地存储", () => {
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem });

    useLayoutStore.getState().setSidebarWidth(5000);

    expect(useLayoutStore.getState().sidebarWidth).toBe(SIDEBAR.max);
    expect(setItem).toHaveBeenCalledTimes(1);
    const [, raw] = setItem.mock.calls[0] as [string, string];
    expect(JSON.parse(raw)).toMatchObject({
      sidebarWidth: SIDEBAR.max,
      chatWidth: CHAT.default,
      chatVisible: true,
    });
  });

  it("显隐切换写回同一份偏好", () => {
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem });

    useLayoutStore.getState().toggleChat();

    expect(useLayoutStore.getState().chatVisible).toBe(false);
    const [, raw] = setItem.mock.calls[0] as [string, string];
    expect(JSON.parse(raw).chatVisible).toBe(false);
  });

  it("本地存储不可写时只丢偏好，不抛错", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota exceeded");
      },
    });

    expect(() => useLayoutStore.getState().setChatWidth(400)).not.toThrow();
    expect(useLayoutStore.getState().chatWidth).toBe(400);
  });
});
