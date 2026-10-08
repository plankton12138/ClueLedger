/**
 * WS 帧协议类型（P3 T6 骨架版）——与 hastings/server/ws.py D3 协议一一对应。
 *
 * 帧方向：
 * - 客户端 → 服务端：CommandFrame（六个命令，见 CommandType）；
 * - 服务端 → 客户端：事件帧（AgentEvent 原样 JSON）或 ResponseFrame；
 * - 事件帧与响应帧靠 type 字段区分："response" 不是任何 tau 事件类型，无碰撞。
 *
 * T7 将把 EventFrame 细化为 AgentEvent 判别联合（字段名按 wire 别名，
 * 如 assistantMessageEvent）；本文件先钉住命令/响应帧契约。
 */

export const COMMAND_TYPES = [
  "prompt",
  "steer",
  "follow_up",
  "abort",
  "get_state",
  "get_messages",
] as const;

export type CommandType = (typeof COMMAND_TYPES)[number];

/** 客户端 → 服务端命令帧。prompt/steer/follow_up 必须携带非空 message。 */
export interface CommandFrame {
  id: number | string;
  type: CommandType;
  message?: string;
}

/** 服务端 → 客户端响应帧（请求-响应配对靠 id 回显）。 */
export interface ResponseFrame {
  type: "response";
  command: CommandType | null;
  success: boolean;
  id?: number | string;
  data?: Record<string, unknown>;
  error?: string;
}

/**
 * 服务端 → 客户端事件帧（骨架版）：T6 阶段只保证 type 可判别、
 * 其余字段原样透传；T7 换成 AgentEvent 判别联合。
 */
export interface EventFrame {
  type: string;
  [field: string]: unknown;
}

/** 服务端 → 客户端所有帧的收窄前形态。 */
export type ServerFrame = EventFrame | ResponseFrame;

export function isResponseFrame(frame: ServerFrame): frame is ResponseFrame {
  return frame.type === "response";
}
