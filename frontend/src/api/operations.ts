/**
 * 统一操作的客户端（1.0.1 P2）。
 *
 * 一个操作就是一次被受理的模型任务：写作、核对、压缩、连接测试都用同一套控制。
 * 这里只放请求与类型，事件归并由使用方处理。
 */

import { requestApi, WorkspaceApiError } from "./workspace";

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

/** 已提交的收据：Git 记录、压缩记录或连接测试结果，按用途给不同的字段。 */
export interface OperationReceipt {
  kind?: string;
  at?: string;
  outcome?: {
    model?: string;
    response?: "passed" | "failed" | "untested";
    tool_call?: "passed" | "failed" | "untested";
    code?: string | null;
    message?: string;
  };
  record?: unknown;
  [key: string]: unknown;
}

export interface OperationResult {
  operation_id: string;
  status: OperationStatus;
  note: string;
  receipts: OperationReceipt[];
  pending_units: string[];
  changed_paths: string[];
  question: { question?: string; question_id?: string } | null;
}

/** 控制请求的键：同一次点击重试沿用同一个键，服务端据此去重。 */
export function operationKey(prefix: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

// 控制面读不到时要能自己收尾：请求一直挂着，界面既发不出下一条，也停不下来。
const CONTROL_TIMEOUT_MS = 8000;

function timed<T>(path: string, init: RequestInit, where: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONTROL_TIMEOUT_MS);
  return requestApi<T>(path, { ...init, signal: controller.signal })
    .catch((error: unknown) => {
      if (!controller.signal.aborted) throw error;
      throw new WorkspaceApiError(
        "OPERATION_TIMEOUT",
        `${Math.round(CONTROL_TIMEOUT_MS / 1000)} 秒内没有返回`,
        "再试一次；持续没有响应时刷新页面后查看活动记录",
        0,
        where,
      );
    })
    .finally(() => clearTimeout(timer));
}

/** 这一会话最近的一次操作：刷新后恢复「继续」入口，不重放旧任务。 */
export async function latestOperation(conversationId: string): Promise<Operation | null> {
  const items = await timed<Operation[]>(
    `/api/operations?conversation_id=${encodeURIComponent(conversationId)}`,
    {},
    "任务列表",
  );
  return items[0] ?? null;
}

/** 读取快照：只读，不触发模型。 */
export function readOperation(operationId: string): Promise<Operation> {
  return timed<Operation>(`/api/operations/${encodeURIComponent(operationId)}`, {}, "任务状态");
}

/** 读取结果：已提交收据、改动路径与待回答问题；同样不触发模型。 */
export function readOperationResult(operationId: string): Promise<OperationResult> {
  return requestApi<OperationResult>(`/api/operations/${encodeURIComponent(operationId)}/result`);
}

/** 暂停：先撤销这一代的写入资格，再取消远端请求。 */
export function pauseOperation(operationId: string, requestKey: string): Promise<Operation> {
  return timed<Operation>(
    `/api/operations/${encodeURIComponent(operationId)}/pause`,
    { method: "POST", body: JSON.stringify({ request_key: requestKey }) },
    "暂停任务",
  );
}

/** 继续：开新的一代；已写出的内容保留。 */
export function continueOperation(
  operationId: string,
  requestKey: string,
  expectedGeneration: number,
): Promise<Operation> {
  return timed<Operation>(
    `/api/operations/${encodeURIComponent(operationId)}/continue`,
    {
      method: "POST",
      body: JSON.stringify({ request_key: requestKey, expected_generation: expectedGeneration }),
    },
    "继续任务",
  );
}

/** 插话：暂停、追加要求、登记新代次是一次请求。 */
export function interjectOperation(
  operationId: string,
  requestKey: string,
  expectedGeneration: number,
  text: string,
): Promise<Operation> {
  return timed<Operation>(
    `/api/operations/${encodeURIComponent(operationId)}/interject`,
    {
      method: "POST",
      body: JSON.stringify({
        request_key: requestKey,
        expected_generation: expectedGeneration,
        text,
      }),
    },
    "插话",
  );
}
