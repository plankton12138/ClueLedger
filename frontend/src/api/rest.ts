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
export async function fetchBible(slug: string, type: BibleType): Promise<BibleResponse> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/bible/${type}`);
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

// ---------- 工作区面（P4 T6）：列表 + 新建（唯一写 REST，资源创建） ----------

/** GET /api/workspaces → 已有工作区 slug 列表（服务端已按目录名排序）。 */
export async function fetchWorkspaces(): Promise<string[]> {
  const res = await fetch("/api/workspaces");
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(`工作区列表读取失败：HTTP ${res.status}`);
  }
  if (body === null || typeof body !== "object" || !Array.isArray((body as { workspaces?: unknown }).workspaces)) {
    throw new Error(
      `工作区列表响应结构不符：期望 { workspaces: string[] }，收到 ${JSON.stringify(body)}。` +
        `怎样算对：确认服务端为 P4 多工作区版本。`,
    );
  }
  return (body as { workspaces: unknown[] }).workspaces.map(String);
}

/** POST /api/workspaces 新建工作区；422（slug 不合法）/ 409（撞名）detail 原样透出。 */
export async function createWorkspace(slug: string): Promise<void> {
  const res = await fetch("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug }),
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new Error(detail);
  }
}

// ---------- 章节面（P5 T1）：列表 + 单章正文（只读） ----------

export interface ChaptersResponse {
  chapters: string[];
}

export interface ChapterResponse {
  id: string;
  content: string;
  revision: number;
}

/** 段落级变更（D3 wire camelCase：oldIndex/newIndex/oldText/newText）。 */
export type DiffKind = "added" | "removed" | "modified";

export interface DiffHunkWire {
  kind: DiffKind;
  oldIndex: number | null;
  newIndex: number | null;
  oldText: string;
  newText: string;
}

export interface ChapterSavedResponse {
  id: string;
  revision: number;
  diff: DiffHunkWire[];
}

/** 从非 2xx 响应体抽 detail（FastAPI 错误统一挂 detail 字段）。 */
async function errorDetail(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null);
  return body !== null && typeof body === "object" && "detail" in body
    ? String((body as { detail: unknown }).detail)
    : `HTTP ${res.status}`;
}

/** GET /api/{slug}/chapters → 章节 id 列表（服务端已按文件名排序）；空工作区 → 空数组。 */
export async function fetchChapters(slug: string): Promise<string[]> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/chapters`);
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(`章节列表读取失败：HTTP ${res.status}`);
  }
  if (
    body === null ||
    typeof body !== "object" ||
    !Array.isArray((body as { chapters?: unknown }).chapters)
  ) {
    throw new Error(
      `章节列表响应结构不符：期望 { chapters: string[] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return (body as { chapters: unknown[] }).chapters.map(String);
}

/** GET /api/{slug}/chapters/{id} → 章节正文与当前 revision；404/422 detail 原样透出。 */
export async function fetchChapter(slug: string, chapterId: string): Promise<ChapterResponse> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(chapterId)}`,
  );
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new Error(`章节 ${chapterId} 读取失败：${detail}`);
  }
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { content?: unknown }).content !== "string" ||
    typeof (body as { revision?: unknown }).revision !== "number"
  ) {
    throw new Error(
      `章节响应结构不符：期望 { id, content, revision }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as ChapterResponse;
}

// ---------- 受管写端点 + 作者草稿锁（P5 T2/T3 契约，T5 前端接线） ----------

/**
 * PUT /api/{slug}/chapters/{id}：作者受管写端点（D3 管线）。
 * 409（写作流冲突 / revision 过期）、422（标题格式）、404（章节不存在）、
 * 500（IO）的 detail 文案原样透出——泄压阀三情形的归因由服务端给出。
 */
export async function saveChapter(
  slug: string,
  chapterId: string,
  content: string,
  baseRevision: number,
): Promise<ChapterSavedResponse> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(chapterId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, baseRevision }),
    },
  );
  if (!res.ok) {
    throw new Error(`章节 ${chapterId} 保存失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { revision?: unknown }).revision !== "number" ||
    !Array.isArray((body as { diff?: unknown }).diff)
  ) {
    throw new Error(
      `章节保存响应结构不符：期望 { id, revision, diff[] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as ChapterSavedResponse;
}

/** PUT .../edit-mark：作者开始编辑 → 加草稿锁（D4 幂等）；失败 detail 原样透出。 */
export async function acquireEditMark(slug: string, chapterId: string): Promise<void> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(chapterId)}/edit-mark`,
    { method: "PUT" },
  );
  if (!res.ok) {
    throw new Error(`章节 ${chapterId} 加锁失败：${await errorDetail(res)}`);
  }
}

/** DELETE .../edit-mark：放弃草稿 → 释放锁（D4 幂等）。 */
export async function releaseEditMark(slug: string, chapterId: string): Promise<void> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(chapterId)}/edit-mark`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error(`章节 ${chapterId} 释放锁失败：${await errorDetail(res)}`);
  }
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
