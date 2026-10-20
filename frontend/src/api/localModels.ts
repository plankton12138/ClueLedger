/**
 * 本地模型任务客户端（V03-1.4）——/api/local-model-tasks 四端点。
 *
 * 核心原则「下载完成 ≠ 加载完成 ≠ 测试通过」在 UI 层的落法：
 * - 阶段即事实：wire 的 stage 是服务端给出的中文阶段文案，percent 可空
 *   （无进度数时只显示阶段，不伪造百分比）；
 * - 轮询而非流：进度靠 1s 间隔回读任务记录，终态即停；
 * - 恢复靠重检：?active=true 返回的记录已由服务端重检实际状态，前端
 *   不凭旧百分比宣称「运行中」。
 */

import { request } from "./v2";

/** 一条本地模型任务记录（snake_case，与 rest.py LocalModelTaskResponse 同形）。 */
export interface LocalModelTaskWire {
  task_id: string;
  request_id: string;
  action: "download" | "test";
  model: string;
  provider: string;
  base_url: string;
  state: "queued" | "running" | "succeeded" | "failed" | "stopped";
  stage: string;
  percent: number | null;
  detail: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface LocalModelTaskCreateInput {
  requestId: string;
  action: "download" | "test";
  model: string;
  /** 已保存连接（provider 档案）名；本地模型固定为 "ollama"。 */
  connectionId: string;
}

export async function createLocalModelTask(
  input: LocalModelTaskCreateInput,
): Promise<LocalModelTaskWire> {
  return request<LocalModelTaskWire>("/api/local-model-tasks", {
    request_id: input.requestId,
    action: input.action,
    model: input.model,
    connection_id: input.connectionId,
  });
}

export async function fetchLocalModelTask(
  taskId: string,
): Promise<LocalModelTaskWire> {
  return request<LocalModelTaskWire>(
    `/api/local-model-tasks/${encodeURIComponent(taskId)}`,
  );
}

/** 取消任务；返回值如实描述取消范围（仅限本应用发起，Ollama 侧拉取可续传）。 */
export async function cancelLocalModelTask(
  taskId: string,
): Promise<LocalModelTaskWire> {
  return request<LocalModelTaskWire>(
    `/api/local-model-tasks/${encodeURIComponent(taskId)}/cancel`,
    {},
  );
}

/** 恢复进行中任务（?active=true）：服务端读路径重检实际状态后返回。 */
export async function fetchActiveLocalModelTasks(): Promise<
  LocalModelTaskWire[]
> {
  const data = await request<{ tasks: LocalModelTaskWire[] }>(
    "/api/local-model-tasks?active=true",
  );
  return data.tasks;
}

const TERMINAL_STATES: ReadonlySet<LocalModelTaskWire["state"]> = new Set([
  "succeeded",
  "failed",
  "stopped",
]);

export function isTerminalState(task: LocalModelTaskWire): boolean {
  return TERMINAL_STATES.has(task.state);
}

/**
 * 终态通知文案：把服务端归因转成用户可读的一句话。
 * succeeded 用 detail（区分「下载完成」与「重检发现已装」），
 * failed 优先 error（三要素归因），stopped 用 detail（取消范围）。
 */
export function taskNotice(task: LocalModelTaskWire): {
  kind: "ok" | "error";
  text: string;
} {
  if (task.state === "succeeded") {
    return {
      kind: "ok",
      text:
        task.action === "test"
          ? `模型 ${task.model} 测试调用通过，可点「设为当前模型」启用。`
          : `模型 ${task.model} 下载完成。注意：下载完成不等于加载完成，更不等于测试通过，可用「测试调用」验证。`,
    };
  }
  if (task.state === "failed") {
    return { kind: "error", text: task.error ?? task.detail };
  }
  return { kind: "ok", text: task.detail };
}
