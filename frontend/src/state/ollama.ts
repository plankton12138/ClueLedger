/**
 * Ollama 本地模型的流解析与展示纯函数（D11）。
 *
 * 本模块零依赖——api/rest.ts 的 pullOllamaModel 读流时复用这里的行切分与
 * 帧解析，组件复用体积/百分比格式化；vitest 钉跨 chunk 断行、error 帧与
 * 100% 裁剪（进度显示是纯展示逻辑，服务端不裁剪）。
 */

/** pull 流一帧（服务端归一化口径）：{status, completed?, total?} 或 {error}。 */
export interface PullFrame {
  status: string;
  completed?: number;
  total?: number;
  error?: string;
}

/**
 * 跨 chunk 按行切分：返回完整行与新余量。网络流不保证按行到达——
 * buffer 必须跨 chunk 保留，末尾无换行的残行留给下一块。
 */
export function parsePullLines(
  buffer: string,
  chunk: string,
): { lines: string[]; buffer: string } {
  const parts = (buffer + chunk).split("\n");
  const remainder = parts.pop() ?? "";
  return { lines: parts.filter((line) => line.trim() !== ""), buffer: remainder };
}

/** 解析一帧 JSON 行；坏帧返回 null（不致命，调用方跳过）。 */
export function parsePullFrame(line: string): PullFrame | null {
  try {
    const raw: unknown = JSON.parse(line);
    if (raw === null || typeof raw !== "object") return null;
    const record = raw as Record<string, unknown>;
    if (typeof record.error === "string" && record.error !== "") {
      return { status: "", error: record.error };
    }
    if (typeof record.status !== "string") return null;
    return {
      status: record.status,
      completed: typeof record.completed === "number" ? record.completed : undefined,
      total: typeof record.total === "number" ? record.total : undefined,
    };
  } catch {
    return null;
  }
}

/** 进度百分比：缺数字/total 非正返回 null（未知进度态）；结果裁剪到 0-100。 */
export function pullPercent(frame: PullFrame): number | null {
  if (typeof frame.completed !== "number" || typeof frame.total !== "number") return null;
  if (frame.total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((frame.completed / frame.total) * 100)));
}

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
