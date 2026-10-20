/**
 * Ollama 本地模型的展示纯函数（D11）。
 *
 * 本模块零依赖——组件复用体积格式化与三态 UI 映射。V03-1.4 起下载进度
 * 改走任务记录（/api/local-model-tasks，见 api/localModels.ts），原 pull
 * 流的行切分/帧解析已随 POST /api/ollama/pull 收敛删除。
 */

/** 字节数人性化显示；非正数显示「—」。 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

/** 检测态 → UI 态：unknown（未检测/检测中）/ guide（引导安装）/ start（启动提示）/ ready。 */
export type OllamaUiState = "unknown" | "guide" | "start" | "ready";

export function uiStateFromStatus(state: string | null | undefined): OllamaUiState {
  switch (state) {
    case "running":
      return "ready";
    case "installed_not_running":
      return "start";
    case "not_installed":
      return "guide";
    default:
      return "unknown";
  }
}
