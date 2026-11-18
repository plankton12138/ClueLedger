/** 界面展示用；落盘仍用 smoke / gate。 */
export function modeLabel(mode: string | null | undefined): string {
  if (mode === "gate") return "正式验收 · 每案 3 次";
  if (mode === "smoke") return "快速检查 · 每案 1 次";
  return mode || "—";
}

export function modeShort(mode: string | null | undefined): string {
  if (mode === "gate") return "正式验收";
  if (mode === "smoke") return "快速检查";
  return mode || "—";
}

export function gateLabel(passed: boolean | null | undefined): string {
  if (passed == null) return "—";
  return passed ? "验收通过" : "验收未通过";
}
