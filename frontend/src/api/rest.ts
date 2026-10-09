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

// ---------- 故事账本统计（P6 T7，对应 rest.py stats 端点，D7） ----------

/** 逾期伏笔条目：名字 + 埋设/预计回收两章锚点。 */
export interface ForeshadowOverdueWire {
  name: string;
  plant_chapter: string;
  expected_resolve_chapter: string;
}

/** 逐条伏笔风险（T8 表格风险列数据源）：overdue/near/normal。 */
export interface ForeshadowRiskItemWire {
  name: string;
  risk: "overdue" | "near" | "normal";
}

/** 全工作区统计（纯派生，snake_case 口径）：统计卡数据源。 */
export interface StoryStatsWire {
  chapters: number;
  total_chars: number;
  latest_chapter: number;
  foreshadow_counts: Record<string, number>;
  foreshadow_total: number;
  foreshadow_resolved: number;
  foreshadow_recovery_rate: number;
  overdue_foreshadows: ForeshadowOverdueWire[];
  foreshadow_risks: ForeshadowRiskItemWire[];
  clue_counts: Record<string, number>;
  clue_total: number;
  clue_red_herring: number;
  clue_red_herring_ratio: number;
}

/** GET /api/{slug}/stats → 账本统计；422（账本损坏）/500（IO）detail 原样透出。 */
export async function fetchStats(slug: string): Promise<StoryStatsWire> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/stats`);
  if (!res.ok) {
    throw new Error(`账本统计读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.chapters !== "number" ||
    typeof record.total_chars !== "number" ||
    typeof record.foreshadow_total !== "number" ||
    typeof record.foreshadow_recovery_rate !== "number" ||
    !Array.isArray(record.overdue_foreshadows) ||
    !Array.isArray(record.foreshadow_risks)
  ) {
    throw new Error(
      `账本统计响应结构不符：期望 { chapters, total_chars, foreshadow_*, overdue_foreshadows, ... }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as StoryStatsWire;
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

/**
 * DELETE /api/workspaces/{slug}?confirm={slug}（P7 T13/T14）：整目录删除，不可恢复。
 * 422（confirm 不一致 / slug 不合法）、404（不存在）、409（写作流运行中）、
 * 500（IO）detail 原样透出——泄压阀三情形归因由服务端给出。
 */
export async function deleteWorkspace(slug: string): Promise<void> {
  const res = await fetch(
    `/api/workspaces/${encodeURIComponent(slug)}?confirm=${encodeURIComponent(slug)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error(`工作区 ${slug} 删除失败：${await errorDetail(res)}`);
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

// ---------- 工作区设置（P6 T2/T3）：写作语言 + 模型端点档案（snake_case 口径） ----------

/** 预设写作语言；缺省中文，取值非法服务端 422 三要素。 */
export const WRITING_LANGUAGES = ["中文", "English", "日本語"] as const;

export type WritingLanguage = (typeof WRITING_LANGUAGES)[number];

/** GET/PUT settings 响应：写作语言 + 生效档案 + 生效模型（P6 T2）。 */
export interface SettingsResponse {
  writing_language: string;
  provider_profile: string;
  model: string;
}

/** 设置局部更新请求：writing_language 必填，档案/模型缺省则保持当前值。 */
export interface SettingsUpdate {
  writing_language: string;
  provider_profile: string;
  model: string;
}

function parseSettings(body: unknown): SettingsResponse {
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.writing_language !== "string" ||
    typeof record.provider_profile !== "string" ||
    typeof record.model !== "string"
  ) {
    throw new Error(
      `设置响应结构不符：期望 { writing_language, provider_profile, model }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as SettingsResponse;
}

/** GET /api/{slug}/settings → 当前设置（含生效档案与模型）；旧工作区回落默认。 */
export async function fetchSettings(slug: string): Promise<SettingsResponse> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/settings`);
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(`设置读取失败：${await errorDetail(res)}`);
  }
  return parseSettings(body);
}

/**
 * PUT /api/{slug}/settings → 落盘并生效；422（非法取值/未知档案/缺 key/无模型）、
 * 409（写作流进行中）、500（IO）detail 原样透出——泄压阀三情形归因由服务端给出。
 */
export async function saveSettings(slug: string, update: SettingsUpdate): Promise<SettingsResponse> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) {
    throw new Error(`设置保存失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  return parseSettings(body);
}

// ---------- provider 档案目录 + 模型发现（P6 T2/T3）：key 只进不出 ----------

/** 档案目录条目：key 仅尾 4 位提示（服务端已脱敏，前端永不接触完整 key）。 */
export interface ProviderSummary {
  name: string;
  base_url: string;
  default_model: string;
  key_hint: string;
  available: boolean;
}

/** GET /api/providers → 服务级档案目录（config.yaml 或 env 回落单档案）。 */
export async function fetchProviders(): Promise<ProviderSummary[]> {
  const res = await fetch("/api/providers");
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(`档案目录读取失败：HTTP ${res.status}`);
  }
  const providers = (body as { providers?: unknown } | null)?.providers;
  if (!Array.isArray(providers)) {
    throw new Error(
      `档案目录响应结构不符：期望 { providers: [] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return providers as ProviderSummary[];
}

/**
 * GET /api/providers/{name}/models → 拉取候选模型（兼作连通测试：通即 key/网络/
 * base_url 全链路可用）。失败 detail 原样透出；空数组为合法空结果（手动输入）。
 */
export async function fetchProviderModels(name: string): Promise<string[]> {
  const res = await fetch(`/api/providers/${encodeURIComponent(name)}/models`);
  if (!res.ok) {
    // 先判失败再读 body——errorDetail 自身消费 response body，不可二次读取。
    throw new Error(await errorDetail(res));
  }
  const body: unknown = await res.json().catch(() => null);
  const models = (body as { models?: unknown } | null)?.models;
  if (!Array.isArray(models)) {
    throw new Error(
      `模型列表响应结构不符：期望 { models: string[] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return models.map(String);
}

// ---------- provider 档案 CRUD（P7 T12/T14）：设置页增删改 + 连通测试 ----------

/** 档案新增/修改请求体（P7 T12）：api_key 留空 = 修改时保留旧 key。 */
export interface ProviderUpsert {
  base_url: string;
  api_key: string;
  api_key_env: string;
  default_model: string;
}

/**
 * PUT /api/providers/{name} → upsert（存在即修改，否则新建）。
 * 422（base_url 空）/ 500（config.yaml 写盘失败）detail 原样透出。
 */
export async function upsertProvider(name: string, body: ProviderUpsert): Promise<ProviderSummary> {
  const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`档案 ${name} 保存失败：${await errorDetail(res)}`);
  }
  const json: unknown = await res.json().catch(() => null);
  const record = json as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.name !== "string" ||
    typeof record.base_url !== "string" ||
    typeof record.available !== "boolean"
  ) {
    throw new Error(
      `档案保存响应结构不符：期望 ProviderSummary，收到 ${JSON.stringify(json)}。`,
    );
  }
  return json as ProviderSummary;
}

/**
 * DELETE /api/providers/{name}：404（不存在）/ 409（引导档案 / 最后档案 /
 * 被文章引用）detail 原样透出——三保护文案由服务端给出。
 */
export async function deleteProvider(name: string): Promise<void> {
  const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(await errorDetail(res));
  }
}

/**
 * POST /api/providers/test：未保存表单的连通测试（P7 T12）。
 * 422（缺 key / key 无效）、502（网络/上游故障）detail 原样透出；
 * 空数组为合法空结果（手动输入模型名）。
 */
export async function testProvider(body: {
  base_url: string;
  api_key: string;
  api_key_env: string;
}): Promise<string[]> {
  const res = await fetch("/api/providers/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await errorDetail(res));
  }
  const json: unknown = await res.json().catch(() => null);
  const models = (json as { models?: unknown } | null)?.models;
  if (!Array.isArray(models)) {
    throw new Error(
      `连通测试响应结构不符：期望 { models: string[] }，收到 ${JSON.stringify(json)}。`,
    );
  }
  return models.map(String);
}

// ---------- 事件流历史（P6 T5，对应 rest.py events 端点，D4/D5） ----------

/** 一条持久化事件：ts/seq/type 为索引列，data 为帧原文（与 WS 帧同形）。 */
export interface EventRecordWire {
  ts: string;
  seq: number;
  type: string;
  data: Record<string, unknown>;
}

/** 事件流分页结果：events 倒序（新→旧）；total/counts 是整账统计。 */
export interface EventsPageWire {
  events: EventRecordWire[];
  has_more: boolean;
  total: number;
  counts: Record<string, number>;
}

/** 事件流查询参数：beforeSeq 向上翻页游标；category 服务端过滤。 */
export interface EventsQuery {
  limit?: number;
  beforeSeq?: number;
  category?: "message" | "tool" | "error";
}

/**
 * GET /api/{slug}/events → 倒序分页 + 分类过滤 + 整账统计。
 * 422（limit/category 越界）detail 原样透出；空账本是合法空结果。
 */
export async function fetchEvents(slug: string, query: EventsQuery = {}): Promise<EventsPageWire> {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.beforeSeq !== undefined) params.set("before_seq", String(query.beforeSeq));
  if (query.category !== undefined) params.set("category", query.category);
  const qs = params.toString();
  const res = await fetch(`/api/${encodeURIComponent(slug)}/events${qs === "" ? "" : `?${qs}`}`);
  if (!res.ok) {
    throw new Error(`事件历史读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    !Array.isArray(record.events) ||
    typeof record.has_more !== "boolean" ||
    typeof record.total !== "number" ||
    record.counts === null ||
    typeof record.counts !== "object"
  ) {
    throw new Error(
      `事件历史响应结构不符：期望 { events, has_more, total, counts }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as EventsPageWire;
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
