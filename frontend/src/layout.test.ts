import { describe, expect, it } from "vitest";
import { BREAKPOINTS, bibleIsSplit, chatIsSplit, layoutKind } from "./layout";

describe("P9 T8 布局断点映射（D6）", () => {
  it("layoutKind：480/790=narrow，1024/1279=medium，1280+=wide", () => {
    expect(layoutKind(480)).toBe("narrow");
    expect(layoutKind(790)).toBe("narrow");
    expect(layoutKind(BREAKPOINTS.lg - 1)).toBe("narrow");
    expect(layoutKind(BREAKPOINTS.lg)).toBe("medium");
    expect(layoutKind(1100)).toBe("medium");
    expect(layoutKind(BREAKPOINTS.xl - 1)).toBe("medium");
    expect(layoutKind(BREAKPOINTS.xl)).toBe("wide");
    expect(layoutKind(1600)).toBe("wide");
  });

  it("chatIsSplit：lg 起为常驻分栏", () => {
    expect(chatIsSplit(480)).toBe(false);
    expect(chatIsSplit(BREAKPOINTS.lg - 1)).toBe(false);
    expect(chatIsSplit(BREAKPOINTS.lg)).toBe(true);
  });

  it("bibleIsSplit：xl 起为常驻分栏", () => {
    expect(bibleIsSplit(1024)).toBe(false);
    expect(bibleIsSplit(BREAKPOINTS.xl - 1)).toBe(false);
    expect(bibleIsSplit(BREAKPOINTS.xl)).toBe(true);
  });

  it("断点值与 Tailwind lg/xl 一致", () => {
    expect(BREAKPOINTS.lg).toBe(1024);
    expect(BREAKPOINTS.xl).toBe(1280);
  });
});
