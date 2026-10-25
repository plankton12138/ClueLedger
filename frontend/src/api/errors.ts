import { WorkspaceApiError } from "./workspace";
import { ModelApiError } from "./models";
export function errorMessage(error: unknown, where: string): string {
  const sentence = (text: string) => text.trim().replace(/[。.!！]+$/, "");
  if (error instanceof WorkspaceApiError || error instanceof ModelApiError) return `[${error.code}] ${error.where ?? where}：${sentence(error.why)}。建议：${sentence(error.fix)}。`;
  if (error instanceof Error && /^\[[A-Z_0-9]+\]/.test(error.message)) return error.message;
  return `[REQUEST_FAILED] ${where}：请求未完成。建议：保留当前内容并重试；持续失败时查看活动记录。`;
}
