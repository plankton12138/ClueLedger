import tailwindcssAnimate from "tailwindcss-animate";
import { theme } from "./src/theme";

/**
 * Tailwind 主题（经典 OS X Aqua 浅色，保留复古对话气泡）。
 * 颜色事实源唯一：src/theme.ts。shadcn 语义名（primary/background/...）
 * 只是 theme.ts 色值的别名，供 components/ui/ 消费，不引入第二事实源。
 * 拟物渐变与内阴影不进令牌，落在 index.css 组件类。
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontSize: { xs: ["13px", "1.6"], sm: ["15px", "1.6"] },
      colors: {
        // CSS theme() 使用令牌原名；工具类同时提供下方 kebab-case 别名。
        ...theme.colors,
        // --- 具名令牌（与 theme.ts 一一对应）---
        page: theme.colors.page,
        workbench: theme.colors.workbench,
        fg: theme.colors.fg,
        "fg-strong": theme.colors.fgStrong,
        sub: theme.colors.sub,
        mute: theme.colors.mute,
        faint: theme.colors.faint,
        "chrome-high": theme.colors.chromeHigh,
        "chrome-mid": theme.colors.chromeMid,
        "chrome-band": theme.colors.chromeBand,
        "chrome-low": theme.colors.chromeLow,
        "chrome-edge": theme.colors.chromeEdge,
        "chrome-shadow": theme.colors.chromeShadow,
        "nav-edge": theme.colors.navEdge,
        "panel-edge": theme.colors.panelEdge,
        "panel-head": theme.colors.panelHead,
        "panel-head-edge": theme.colors.panelHeadEdge,
        "row-edge": theme.colors.rowEdge,
        live: theme.colors.live,
        badge: theme.colors.badge,
        count: theme.colors.count,
        green: theme.colors.green,
        "green-bg": theme.colors.greenBg,
        "green-edge": theme.colors.greenEdge,
        amber: theme.colors.amber,
        "amber-bg": theme.colors.amberBg,
        "amber-edge": theme.colors.amberEdge,
        red: theme.colors.red,
        "red-bg": theme.colors.redBg,
        "red-edge": theme.colors.redEdge,
        blue: theme.colors.blue,
        "blue-bg": theme.colors.blueBg,
        "blue-edge": theme.colors.blueEdge,
        "notice-edge": theme.colors.noticeEdge,
        "notice-text": theme.colors.noticeText,
        "hero-high": theme.colors.heroHigh,
        "hero-low": theme.colors.heroLow,
        "del-bg": theme.colors.delBg,
        "del-text": theme.colors.delText,
        "ins-bg": theme.colors.insBg,
        "ins-text": theme.colors.insText,
        sheet: theme.colors.sheet,
        "sheet-ink": theme.colors.sheetInk,
        "sheet-mute": theme.colors.sheetMute,
        "sheet-line": theme.colors.sheetLine,
        "sel-high": theme.colors.selHigh,
        "sel-low": theme.colors.selLow,
        "seg-edge": theme.colors.segEdge,
        "foot-high": theme.colors.footHigh,
        "foot-low": theme.colors.footLow,
        "foot-text": theme.colors.footText,
        "foot-ok": theme.colors.footOk,
        // --- shadcn 语义别名（值仍出自 theme.ts）---
        background: theme.colors.page,
        surface: "#FFFFFF",
        foreground: theme.colors.fg,
        primary: {
          DEFAULT: theme.colors.btnLow,
          foreground: "#FFFFFF",
        },
        muted: {
          DEFAULT: "#EDF1F3",
          foreground: theme.colors.sub,
        },
        accent: {
          DEFAULT: theme.colors.selHigh,
          foreground: theme.colors.fgStrong,
        },
        danger: {
          DEFAULT: theme.colors.red,
          foreground: "#FFFFFF",
        },
        success: theme.colors.green,
        info: theme.colors.blue,
        overlay: "rgba(39, 59, 75, 0.45)",
      },
      fontFamily: {
        serif: theme.fonts.serif,
        sans: theme.fonts.sans,
      },
      boxShadow: {
        // portal 浮层（Select/Popover/Dialog/Sheet）统一投影
        surface: "0 12px 32px rgba(37, 64, 81, 0.28)",
      },
      zIndex: {
        overlay: "50",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
