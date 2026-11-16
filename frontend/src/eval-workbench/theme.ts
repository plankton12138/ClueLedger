export type WbTheme = "light" | "dark";

const STORAGE_KEY = "clueledger.eval-workbench.theme";

export function readStoredTheme(): WbTheme | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
  } catch {
    /* ignore */
  }
  return null;
}

export function systemTheme(): WbTheme {
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

export function resolveTheme(stored: WbTheme | null = readStoredTheme()): WbTheme {
  return stored ?? systemTheme();
}

export function applyTheme(theme: WbTheme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function persistTheme(theme: WbTheme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
  applyTheme(theme);
}

export function toggleTheme(current: WbTheme): WbTheme {
  const next = current === "dark" ? "light" : "dark";
  persistTheme(next);
  return next;
}
