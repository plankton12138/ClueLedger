import { theme } from "./src/theme";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
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
        parchment: theme.colors.parchment,
        "parchment-soft": theme.colors.parchmentSoft,
        "parchment-ink": theme.colors.parchmentInk,
        "parchment-mute": theme.colors.parchmentMute,
        "parchment-line": theme.colors.parchmentLine,
      },
      fontFamily: {
        serif: theme.fonts.serif,
        sans: theme.fonts.sans,
      },
    },
  },
  plugins: [],
};
