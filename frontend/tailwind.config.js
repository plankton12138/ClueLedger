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
        overdue: theme.colors.overdue,
      },
      fontFamily: {
        serif: theme.fonts.serif,
        sans: theme.fonts.sans,
      },
    },
  },
  plugins: [],
};
