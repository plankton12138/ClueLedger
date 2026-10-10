/**
 * 布局断点事实源（P9 T8，D6）：宽度 → 布局形态的唯一纯函数口径。
 *
 * - narrow（<lg 1024）：正文唯一常驻，对话/账本双抽屉；
 * - medium（lg 1024–1279）：对话 Splitter + 正文，账本走抽屉；
 * - wide（xl ≥1280）：三栏 Splitter。
 *
 * 组件运行时仍以 matchMedia 监听 CSS 断点（App 常驻监听），本模块供
 * vitest 钉死映射语义，二者断点值必须一致。
 */
export const BREAKPOINTS = {
  lg: 1024,
  xl: 1280,
} as const;

export type LayoutKind = "narrow" | "medium" | "wide";

export function layoutKind(width: number): LayoutKind {
  if (width >= BREAKPOINTS.xl) return "wide";
  if (width >= BREAKPOINTS.lg) return "medium";
  return "narrow";
}

/** 对话栏是否走常驻 Splitter（narrow 时为左缘抽屉）。 */
export function chatIsSplit(width: number): boolean {
  return width >= BREAKPOINTS.lg;
}

/** 账本栏是否走常驻 Splitter（<xl 时为右缘抽屉）。 */
export function bibleIsSplit(width: number): boolean {
  return width >= BREAKPOINTS.xl;
}
