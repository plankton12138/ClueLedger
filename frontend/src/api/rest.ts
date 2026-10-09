/**
 * REST 只读面客户端（P3 T9，对应 rest.py 的 D6 响应模型与 D7 refetch 规则）。
 *
 * 字段名是 snake_case——服务端 `entity.model_dump()` 未开 by_alias（REST 响应
 * 模型未配 alias_generator，与 WS 事件帧的 camelCase 别名体系是两套口径）。
 */

import type { EventFrame } from "./types";

export type BibleType = "character" | "clue" | "foreshadow" | "method" | "timeline";

export const BIBLE_TYPES: BibleType[] = [
  "character",
  "clue",
  "foreshadow",
  "method",
  "timeline",
];

export const BIBLE_LABELS: Record<BibleType, string> = {
  character: "角色",
  clue: "线索",
  foreshadow: "伏笔",
  method: "手法",
  timeline: "时间线",
};

export interface BibleResponse {
  entity_type: string;
  count: number;
  entities: Record<string, unknown>[];
}

// ---------- 五账本实体字段（镜像 models.py 的 model_dump，snake_case） ----------

export interface CharacterEntity {
  name: string;
  identity: string;
  motivation: string;
  secret: string;
  arc: string;
  notes: string;
}

export interface ClueEntity {
  name: string;
  content: string;
  chapter: string;
  points_to: string;
  is_red_herring: boolean;
  is_genuine: boolean;
  status: string;
}

export interface ForeshadowEntity {
  name: string;
  content: string;
  plant_chapter: string;
  plant_mode: string;
  expected_resolve_chapter: string;
  status: string;
}

export interface MethodEntity {
  name: string;
  description: string;
  time_window: string;
  status: string;
}

export interface TimelineEntity {
  name: string;
  day: number;
  t_start: string | null;
  t_end: string | null;
  chapter: string;
  participants: string[];
  note: string;
}

/** 读取一类账本；422（损坏 YAML 等）的 detail 文案原样透出。 */
export async function fetchBible(type: BibleType): Promise<BibleResponse> {
  const res = await fetch(`/api/bible/${type}`);
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new Error(`账本 ${type} 读取失败：${detail}`);
  }
  return body as BibleResponse;
}

// ---------- D7 refetch 规则（纯函数，呈现策略而非业务校验） ----------

/**
 * 写工具成功（isError=false）→ 应刷新的账本集合。
 * 注意 tool_execution_end 帧不带 args（args 只在 start/update 上）——调用方
 * 需提供 toolCallId → args 的查表（由 tool_execution_start 事件养起来）。
 */
export function refetchTargets(
  event: EventFrame,
  argsOf: (toolCallId: string) => Record<string, unknown> | undefined,
): BibleType[] {
  if (event.type !== "tool_execution_end" || event.isError) return [];
  if (event.toolName === "bible_upsert") {
    const t = argsOf(event.toolCallId)?.["entity_type"];
    return typeof t === "string" && (BIBLE_TYPES as string[]).includes(t) ? [t as BibleType] : [];
  }
  if (event.toolName === "method_lock" || event.toolName === "method_unlock") return ["method"];
  return [];
}
