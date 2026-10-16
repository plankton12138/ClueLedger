/**
 * REST 只读面客户端（P3 T9，对应 rest.py 的 D6 响应模型与 D7 refetch 规则）。
 *
 * 字段名是 snake_case——服务端 `entity.model_dump()` 未开 by_alias（REST 响应
 * 模型未配 alias_generator，与 WS 事件帧的 camelCase 别名体系是两套口径）。
 */

import type { EventFrame } from "./types";
import { parsePullFrame, parsePullLines, type PullFrame } from "../state/ollama";
import { MODEL_BACKEND_MISMATCH } from "./compatibility";

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

// ---------- 会话面（P10 T14，D13）：列表/新建/切换/删除/重命名 ----------

/** 一条会话的元数据（snake_case，与 rest.py SessionSummary 同形）。 */
export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  last_active: string;
  message_count: number;
  active: boolean;
}

export interface SessionsResponse {
  sessions: SessionSummary[];
  active_id: string;
}

/** GET /api/{slug}/sessions（P10 D13）：平铺会话列表 + 当前活跃 id。 */
export async function listSessions(slug: string): Promise<SessionsResponse> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/sessions`);
  if (!res.ok) {
    throw new Error(`读取会话列表失败：${await errorDetail(res)}`);
  }
  return (await res.json()) as SessionsResponse;
}

/**
 * POST /api/{slug}/sessions（P10 D13）：新建会话并设为活跃；running → 409。
 * 旧会话保留在列表可回看（收编原 reset 的「开新会话」语义）。
 */
export async function createSession(slug: string, title?: string): Promise<SessionSummary> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
  });
  if (!res.ok) {
    throw new Error(`新建会话失败：${await errorDetail(res)}`);
  }
  return (await res.json()) as SessionSummary;
}

/** POST /api/{slug}/sessions/{id}/activate（P10 D13）：切换活跃会话；running → 409。 */
export async function activateSession(slug: string, sessionId: string): Promise<SessionSummary> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/sessions/${encodeURIComponent(sessionId)}/activate`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new Error(`切换会话失败：${await errorDetail(res)}`);
  }
  return (await res.json()) as SessionSummary;
}

/** DELETE /api/{slug}/sessions/{id}（P10 D13）：删除会话，返回新的活跃 id。 */
export async function deleteSession(
  slug: string,
  sessionId: string,
): Promise<{ slug: string; active_id: string }> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error(`删除会话失败：${await errorDetail(res)}`);
  }
  return (await res.json()) as { slug: string; active_id: string };
}

/** PATCH /api/{slug}/sessions/{id}（P10 D13）：重命名会话（只改元数据）。 */
export async function renameSession(
  slug: string,
  sessionId: string,
  title: string,
): Promise<SessionSummary> {
  const res = await fetch(
    `/api/${encodeURIComponent(slug)}/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );
  if (!res.ok) {
    throw new Error(`重命名会话失败：${await errorDetail(res)}`);
  }
  return (await res.json()) as SessionSummary;
}

// ---------- 手动压缩上下文（P10 T13，D12，对应 rest.py session/compact） ----------

/** 压缩统计（snake_case 口径，与 CompactionResult 同形）。 */
export interface SessionCompactResponse {
  compacted: boolean;
  reason: string;
  tokens_before: number;
  tokens_after: number;
  stripped_chapters: number[];
  summarized: boolean;
  archived: string | null;
}

/**
 * POST /api/{slug}/session/compact（P10 D12）：绕过阈值手动压缩当前会话。
 * running → 409；无可剥离内容 → 200 且 `compacted=false`（合法空结果，非错误）。
 */
export async function compactSession(slug: string): Promise<SessionCompactResponse> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/session/compact`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`压缩上下文失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json();
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { compacted?: unknown }).compacted !== "boolean"
  ) {
    throw new Error(`压缩响应结构不符：期望 { compacted, reason, ... }，收到 ${JSON.stringify(body)}。`);
  }
  return body as SessionCompactResponse;
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
  /** 免密端点（本地模型如 Ollama）：无 key 也可用，提示文案随之区分。 */
  no_auth: boolean;
  catalog_id?: string;
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

/** 内建 provider 目录条目（P7c T15/T16）：登录式接入的预填数据，纯公开信息。 */
export interface CatalogEntry {
  name: string;
  display_name: string;
  base_url: string;
  default_model: string;
  models: string[];
  api_key_env: string;
  key_url: string;
  category: "api" | "coding_plan" | "local";
  no_auth: boolean;
  docs_url: string;
  note: string;
}

/** GET /api/provider-catalog → 内建目录（静态数据，无任何密钥字段）。 */
export async function fetchProviderCatalog(): Promise<CatalogEntry[]> {
  const res = await fetch("/api/provider-catalog");
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(`目录读取失败：HTTP ${res.status}`);
  }
  const catalog = (body as { catalog?: unknown } | null)?.catalog;
  if (!Array.isArray(catalog)) {
    throw new Error(
      `目录响应结构不符：期望 { catalog: [] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  if (
    catalog.some(
      (entry: unknown) =>
        typeof entry === "object" && entry !== null && !("category" in entry),
    )
  ) {
    throw new Error(MODEL_BACKEND_MISMATCH);
  }
  return catalog as CatalogEntry[];
}

/** 档案新增/修改请求体（P7 T12）：api_key 留空 = 修改时保留旧 key。 */
export interface ProviderUpsert {
  base_url: string;
  api_key: string;
  /** 省略时保留既有环境变量来源；显式空串表示清空。 */
  api_key_env?: string;
  default_model: string;
  /** 免密端点（本地模型如 Ollama）：true 时档案无 key 也算可用。 */
  no_auth?: boolean;
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

// ---------- 工作区生命周期快照（P8 T7，D8）：顶栏阶段徽章数据源 ----------

/** 一次阶段迁移留痕（story.yaml stage_history 镜像）。 */
export interface StageTransitionWire {
  from_stage: string;
  to_stage: string;
  reason: string;
  at: string;
}

/** 六账本计数快照（methods_locked 供完备度展示）。 */
export interface StoryCountsWire {
  characters: number;
  clues: number;
  foreshadows: number;
  methods: number;
  methods_locked: number;
  outlines: number;
}

/** GET /api/{slug}/story 响应：chapters.written/planned 供 n/m 进度。 */
export interface StoryWire {
  slug: string;
  stage: string;
  stage_history: StageTransitionWire[];
  counts: StoryCountsWire;
  chapters: { written: number; planned: number };
}

/** 纯只读派生；story.yaml 的写路径只有 story_stage 工具（REST 不暴露写口）。 */
export async function fetchStory(slug: string): Promise<StoryWire> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/story`);
  if (!res.ok) {
    throw new Error(`工作区状态读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.stage !== "string" ||
    !Array.isArray(record.stage_history) ||
    record.counts === null ||
    typeof record.counts !== "object" ||
    record.chapters === null ||
    typeof record.chapters !== "object"
  ) {
    throw new Error(
      `工作区状态响应结构不符：期望 { slug, stage, stage_history, counts, chapters }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as StoryWire;
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

// ---------- 语料库（P7 T6/T7，D6）：作者级文风语料，路径无 slug ----------

/** 语料文件条目：chars 惰性（列表只报 size，字数走 style-profile）。 */
export interface CorpusFileInfoWire {
  name: string;
  size_bytes: number;
  chars: number | null;
  uploaded_at: string;
}

/** 全语料聚合文风档案（确定性统计，零 LLM）；warnings 为逐文件读码告警。 */
export interface StyleProfileWire {
  total_chars: number;
  sentence_count: number;
  avg_sentence_length: number;
  paragraph_count: number;
  avg_paragraph_length: number;
  dialogue_ratio: number;
  sentence_buckets: Record<string, number>;
  warnings: string[];
}

/** GET /api/corpus → 语料清单（按文件名排序）；空库为合法空结果。 */
export async function fetchCorpus(): Promise<CorpusFileInfoWire[]> {
  const res = await fetch("/api/corpus");
  if (!res.ok) {
    throw new Error(`语料清单读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const files = (body as { files?: unknown } | null)?.files;
  if (!Array.isArray(files)) {
    throw new Error(
      `语料清单响应结构不符：期望 { files: [] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return files as CorpusFileInfoWire[];
}

/**
 * POST /api/corpus（multipart）：v1 仅 .txt/.md；422（坏后缀/空文件）、
 * 409（同名）detail 三要素原样透出——覆盖须先显式 DELETE。
 */
export async function uploadCorpus(file: File): Promise<CorpusFileInfoWire> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/corpus", { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(`语料上传失败（${file.name}）：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { name?: unknown }).name !== "string"
  ) {
    throw new Error(
      `语料上传响应结构不符：期望 { name, size_bytes, ... }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as CorpusFileInfoWire;
}

/** DELETE /api/corpus/{name}：404（不存在）/ 422（名字不合法）detail 原样透出。 */
export async function deleteCorpus(name: string): Promise<void> {
  const res = await fetch(`/api/corpus/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(`语料 ${name} 删除失败：${await errorDetail(res)}`);
  }
}

/** GET /api/corpus/style-profile → 聚合档案；空语料 = 全零合法空结果。 */
export async function fetchStyleProfile(): Promise<StyleProfileWire> {
  const res = await fetch("/api/corpus/style-profile");
  if (!res.ok) {
    throw new Error(`文风档案读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.total_chars !== "number" ||
    typeof record.dialogue_ratio !== "number" ||
    record.sentence_buckets === null ||
    typeof record.sentence_buckets !== "object" ||
    !Array.isArray(record.warnings)
  ) {
    throw new Error(
      `文风档案响应结构不符：期望 { total_chars, dialogue_ratio, sentence_buckets, warnings, ... }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as StyleProfileWire;
}

// ---------- 审计中心（P7 T8/T9，D7）：单章确定性审计报告 ----------

/** 二元比率指标：count 语义随指标——回收数 / 公平呈现数。 */
export interface AuditRatioWire {
  rate: number;
  count: number;
  total: number;
}

/** 人物一致性（结构代理）：1 - 冲突数/时间线条目数；空时间线为 null。 */
export interface AuditConsistencyWire {
  rate: number;
  conflicts: number;
  entries: number;
}

/** 一条检查结果：message 三要素；anchor_* 喂「定位问题章节」。 */
export interface AuditCheckWire {
  rule: string;
  severity: "error" | "warn";
  message: string;
  anchor_chapter: string | null;
  anchor_name: string | null;
}

/** 单章审计报告（纯派生无新存储）；三指标是结构代理非语义判断。 */
export interface AuditReportWire {
  chapter_id: string;
  foreshadow_recovery: AuditRatioWire;
  clue_fairness: AuditRatioWire;
  character_consistency: AuditConsistencyWire | null;
  checks: AuditCheckWire[];
}

/** GET /api/{slug}/audit/{chapterId}：404（章不存在）/ 422（id 不合法/账本损坏）。 */
export async function fetchAudit(slug: string, chapterId: string): Promise<AuditReportWire> {
  const res = await fetch(`/api/${slug}/audit/${encodeURIComponent(chapterId)}`);
  if (!res.ok) {
    throw new Error(`审计报告读取失败（${chapterId}）：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.chapter_id !== "string" ||
    !Array.isArray(record.checks) ||
    record.foreshadow_recovery === null ||
    typeof record.foreshadow_recovery !== "object"
  ) {
    throw new Error(
      `审计报告响应结构不符：期望 { chapter_id, checks, ... }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as AuditReportWire;
}

// ---------- D7 refetch 规则（纯函数，呈现策略而非业务校验） ----------

/**
 * 写工具成功（isError=false）→ 应刷新的账本集合。
 * 注意 tool_execution_end 帧不带 args（args 只在 start/update 上）——调用方
 * 需提供 toolCallId → args 的查表（由 tool_execution_start 事件养起来）。
 */
export function refetchTargets(
  _event: EventFrame,
  _argsOf: (toolCallId: string) => Record<string, unknown> | undefined,
): BibleType[] {
  // V1 写工具（bible_upsert/method_lock 等）已退役，账本数据不再经工具帧变化；
  // 保留签名供 ChatStream 的数据刷新调度使用（当前恒为空）。
  return [];
}

// ---------- Ollama 本地模型（D11）：检测 / 目录 / 已装 / 下载流 / 删除 ----------

/** Ollama 三态检测（status 恒 200——不可达是结果数据不是错误）。 */
export interface OllamaStatusWire {
  state: "running" | "installed_not_running" | "not_installed";
  version: string;
  base_url: string;
  install_url: string;
}

/** 预置推荐模型清单条目（纯公开信息，服务端管理）。 */
export interface OllamaCatalogEntryWire {
  name: string;
  params: string;
  size_label: string;
  note: string;
}

/** 一个已安装模型。 */
export interface OllamaModelInfoWire {
  name: string;
  size_bytes: number;
}

/** GET /api/ollama/status → 三态检测结果。 */
export async function fetchOllamaStatus(): Promise<OllamaStatusWire> {
  const res = await fetch("/api/ollama/status");
  if (!res.ok) {
    throw new Error(`Ollama 状态检测失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const record = body as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== "object" ||
    typeof record.state !== "string" ||
    typeof record.version !== "string" ||
    typeof record.base_url !== "string" ||
    typeof record.install_url !== "string"
  ) {
    throw new Error(
      `Ollama 状态响应结构不符：期望 { state, version, base_url, install_url }，` +
        `收到 ${JSON.stringify(body)}。`,
    );
  }
  return body as OllamaStatusWire;
}

/** GET /api/ollama/catalog → 预置推荐清单（静态数据）。 */
export async function fetchOllamaCatalog(): Promise<OllamaCatalogEntryWire[]> {
  const res = await fetch("/api/ollama/catalog");
  if (!res.ok) {
    throw new Error(`预置模型清单读取失败：${await errorDetail(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  const catalog = (body as { catalog?: unknown } | null)?.catalog;
  if (!Array.isArray(catalog)) {
    throw new Error(
      `预置清单响应结构不符：期望 { catalog: [] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return catalog as OllamaCatalogEntryWire[];
}

/** GET /api/ollama/models → 已装模型列表；502（Ollama 不可达）detail 原样透出。 */
export async function fetchOllamaModels(): Promise<OllamaModelInfoWire[]> {
  const res = await fetch("/api/ollama/models");
  if (!res.ok) {
    throw new Error(await errorDetail(res));
  }
  const body: unknown = await res.json().catch(() => null);
  const models = (body as { models?: unknown } | null)?.models;
  if (!Array.isArray(models)) {
    throw new Error(
      `已装模型列表响应结构不符：期望 { models: [] }，收到 ${JSON.stringify(body)}。`,
    );
  }
  return models as OllamaModelInfoWire[];
}

/**
 * POST /api/ollama/pull → 读流式 NDJSON 进度，逐帧回调 onFrame（仅进度帧）；
 * error 帧与「流未送达 success 终帧」抛错（三要素文案服务端给出）。
 * signal 供取消——断开后 Ollama 侧继续下载，重发同一 pull 幂等续传。
 */
export async function pullOllamaModel(
  model: string,
  onFrame: (frame: PullFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ollama/pull", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
    signal,
  });
  if (!res.ok) {
    throw new Error(await errorDetail(res));
  }
  if (!res.body) {
    throw new Error(`下载流不可读：${model}。为什么错：浏览器未返回响应体流。`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let succeeded = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const parsed = parsePullLines(buffer, decoder.decode(value, { stream: true }));
    buffer = parsed.buffer;
    for (const line of parsed.lines) {
      const frame = parsePullFrame(line);
      if (frame === null) continue;
      if (frame.error !== undefined) {
        throw new Error(frame.error);
      }
      if (frame.status === "success") succeeded = true;
      onFrame(frame);
    }
  }
  if (buffer.trim() !== "") {
    const frame = parsePullFrame(buffer);
    if (frame !== null && frame.error === undefined) onFrame(frame);
  }
  if (!succeeded) {
    throw new Error(
      `模型 ${model} 下载流意外中断。` +
        "为什么错：连接在送达完成帧之前断开。" +
        "怎样算对：重新点击下载——pull 支持断点续传，已下载部分不会丢失。",
    );
  }
}

/** DELETE /api/ollama/models/{name}：404（未装）/ 502（上游故障）detail 原样透出。 */
export async function deleteOllamaModel(name: string): Promise<void> {
  const res = await fetch(`/api/ollama/models/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`模型 ${name} 删除失败：${await errorDetail(res)}`);
  }
}
