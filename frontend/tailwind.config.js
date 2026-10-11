import tailwindcssAnimate from "tailwindcss-animate";
import { theme } from "./src/theme";

/**
 * Tailwind 主题。
 * 颜色事实源唯一：src/theme.ts。shadcn 语义名（primary/background/surface/
 * border/foreground/muted/danger/...）只是 theme.ts 色值的别名，
 * 供 components/ui/ 消费，不引入 HSL CSS 变量第二事实源（前端基建改造 P0）。
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // --- 项目既有具名令牌 ---
        ink: theme.colors.ink,
        "ink-soft": theme.colors.inkSoft,
        line: theme.colors.line,
        gold: theme.colors.gold,
        "gold-soft": theme.colors.goldSoft,
        paper: theme.colors.paper,
        planned: theme.colors.planned,
        planted: theme.colors.planted,
        recovered: theme.colors.recovered,
        overdue: theme.colors.overdue,
        violet: theme.colors.violet,
        parchment: theme.colors.parchment,
        "parchment-soft": theme.colors.parchmentSoft,
        "parchment-ink": theme.colors.parchmentInk,
        "parchment-mute": theme.colors.parchmentMute,
        "parchment-line": theme.colors.parchmentLine,
        // --- shadcn 语义别名（值仍出自 theme.ts）---
        background: theme.colors.ink,
        surface: theme.colors.inkSoft,
        foreground: theme.colors.paper,
        primary: {
          DEFAULT: theme.colors.gold,
          foreground: theme.colors.ink,
        },
        muted: {
          DEFAULT: theme.colors.inkSoft,
          // 次级文本：paper/55；Radix portal 内容也走该令牌，不依赖父级继承。
          foreground: "rgba(224, 220, 200, 0.55)",
        },
        accent: {
          DEFAULT: "rgba(255, 255, 255, 0.06)",
          foreground: theme.colors.paper,
        },
        danger: {
          DEFAULT: theme.colors.overdue,
          foreground: theme.colors.paper,
        },
        success: theme.colors.planted,
        info: theme.colors.planned,
        // 弹窗/抽屉遮罩
        overlay: "rgba(10, 14, 28, 0.72)",
      },
      fontFamily: {
        serif: theme.fonts.serif,
        sans: theme.fonts.sans,
      },
      boxShadow: {
        // portal 浮层（Select/Popover/Dialog/Sheet）统一投影
        surface: "0 12px 32px rgba(0, 0, 0, 0.45)",
      },
      zIndex: {
        overlay: "50",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
