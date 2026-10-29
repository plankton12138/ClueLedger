/**
 * 统一操作的客户端（1.0.1 P2）。
 *
 * 一个操作就是一次被受理的模型任务：写作、核对、压缩、连接测试都用同一套控制。
 * 这里只放请求与类型，事件归并规则在 `state/operations.ts`。
 */

import { requestApi } from "./workspace";

export type OperationStatus =
  | "accepted"
  | "running"
  | "paused"
  | "awaiting_answer"
  | "needs_attention"
  | "completed"
  | "failed";

/** 当前状态下可用的控制；服务端给结论，界面不自行推断。 */
export interface OperationCapabilities {
  pause: boolean;
  continue: boolean;
  interject: boolean;
}

export interface OperationError {
  code?: string;
  where?: string;
  why?: string;
  fix?: string;
}

export interface Operation {
  id: string;
  conversation_id: string;
  turn_id: string;
  purpose: string;
  target: string;
  status: OperationStatus;
  generation: number;
  phase: string;
  model: string;
  connection: string;
  note: string;
  error: OperationError | null;
  pending_units: string[];
  last_seq: number;
  created_at: string;
  updated_at: string;
  capabilities: OperationCapabilities;
}

/** 控制请求的键：同一次点击重试沿用同一个键，服务端据此去重。 */
export function operationKey(prefix: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

/** 这一会话最近的一次操作：刷新后恢复「继续」入口，不重放旧任务。 */
export async function latestOperation(conversationId: string): Promise<Operation | null> {
  const items = await requestApi<Operation[]>(
    `/api/operations?conversation_id=${encodeURIComponent(conversationId)}`,
  );
  return items[0] ?? null;
}

/** 读取快照：只读，不触发模型。 */
export function readOperation(operationId: string): Promise<Operation> {
  return requestApi<Operation>(`/api/operations/${encodeURIComponent(operationId)}`);
}

/** 暂停：先撤销这一代的写入资格，再取消远端请求。 */
export function pauseOperation(operationId: string, requestKey: string): Promise<Operation> {
  return requestApi<Operation>(`/api/operations/${encodeURIComponent(operationId)}/pause`, {
    method: "POST",
    body: JSON.stringify({ request_key: requestKey }),
  });
}

/** 继续：开新的一代；已写出的内容保留。 */
export function continueOperation(
  operationId: string,
  requestKey: string,
  expectedGeneration: number,
): Promise<Operation> {
  return requestApi<Operation>(`/api/operations/${encodeURIComponent(operationId)}/continue`, {
    method: "POST",
    body: JSON.stringify({ request_key: requestKey, expected_generation: expectedGeneration }),
  });
}

/** 插话：暂停、追加要求、登记新代次是一次请求。 */
export function interjectOperation(
  operationId: string,
  requestKey: string,
  expectedGeneration: number,
  text: string,
): Promise<Operation> {
  return requestApi<Operation>(`/api/operations/${encodeURIComponent(operationId)}/interject`, {
    method: "POST",
    body: JSON.stringify({
      request_key: requestKey,
      expected_generation: expectedGeneration,
      text,
    }),
  });
}
