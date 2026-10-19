/**
 * 意图区分客户端与只读展示口径（V03-7.1）。
 *
 * 作者一次表达是三种意图之一（计划 M10 §1–4）：讨论（无写权限）/候选/执行
 * （范围明确授权）。分类由服务端确定性给出（`POST /conversations/{id}/intents`），
 * 本模块只做两件事：
 *
 * - 把请求/响应形状钉死为一个 DTO（含 `reuses_authorization`——「同范围不重复审批」）；
 * - 提供纯函数展示口径（`intentSummary` 等），组件与测试共用，不各自解释一遍。
 *
 * 本模块不含任何写端口：分类是只读的，`discussion`/`candidate` 恒不改正式内容
 * （`write_scope` 为 null）——界面上也必须这样呈现，不得显示成会写入的执行意图。
 */

import { projectPrefix, request } from "./v2";

export type IntentType = "discussion" | "candidate" | "execution";

export const intentLabels: Record<IntentType, string> = {
  discussion: "讨论",
  candidate: "候选",
  execution: "执行",
};

/** 影响执行的关键选择（最多 1–2 个）；范围已知时为空数组。 */
export interface IntentKeyChoice {
  where: string;
  question: string;
  options: string[];
}

export interface IntentPayload {
  intent: IntentType;
  function: string | null;
  analysis_scope: string[];
  /** 只有执行意图才非 null；讨论/候选一律 null。 */
  write_scope: string[] | null;
  requires_choice: boolean;
  choices: IntentKeyChoice[];
  reason: string;
  source: "author_message";
  source_message_id: string | null;
  /** true = 与本次会话已启动的同范围任务一致，调用方不再追加确认。 */
  reuses_authorization: boolean;
}

/** 分类输入：给 `text` 预览当前输入；给 `messageId` 用已保存的作者消息。 */
export interface ClassifyIntentInput {
  text?: string;
  messageId?: string;
}

/** 只读分类：不创建任务、不调用模型。 */
export async function classifyIntent(
  projectId: string,
  conversationId: string,
  input: ClassifyIntentInput,
): Promise<IntentPayload> {
  const body =
    input.text !== undefined
      ? { text: input.text }
      : { message_id: input.messageId };
  return request<IntentPayload>(
    `${projectPrefix(projectId)}/conversations/${encodeURIComponent(conversationId)}/intents`,
    body,
  );
}

/** 执行功能的中文口径；讨论/候选无功能或为候选生成。 */
export function intentFunctionLabel(fn: string | null): string {
  if (fn === "holistic_revision") return "整体调整";
  if (fn === "revision") return "定向修订";
  if (fn === "draft") return "生成候选";
  return "";
}

/** 只有执行意图、且有非空写范围时，才允许写入正式内容。 */
export function canWriteFormalContent(payload: IntentPayload): boolean {
  return payload.intent === "execution" && (payload.write_scope?.length ?? 0) > 0;
}

/** 需要作者先选范围：歧义只问影响执行的关键选择，最多 1–2 个。 */
export function needsScopeChoice(payload: IntentPayload): boolean {
  return payload.intent === "execution" && payload.requires_choice;
}

/** 同范围已授权：继续执行不再重复审批。 */
export function reusesAuthorization(payload: IntentPayload): boolean {
  return payload.intent === "execution" && payload.reuses_authorization;
}

/** 一行说明：讨论/候选明确只读，执行给出功能与写范围或待选范围。 */
export function intentSummary(payload: IntentPayload): string {
  if (payload.intent === "discussion") return "只回应，不改正式内容";
  if (payload.intent === "candidate") return "生成可比较的方案，不改正式内容";
  const fn = intentFunctionLabel(payload.function);
  const head = fn ? `${fn}：` : "";
  if (payload.reuses_authorization) {
    return `${head}同范围已授权，无需重复审批`;
  }
  const scope = payload.write_scope ?? [];
  if (scope.length > 0) return `${head}写范围 ${scope.join("、")}`;
  return payload.choices.length > 0
    ? `写范围待选定（${payload.choices.length} 个关键选择）`
    : "写范围待确认";
}
