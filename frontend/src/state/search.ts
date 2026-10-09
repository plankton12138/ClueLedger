/**
 * 全局搜索纯函数（P6 T11，计划 D9）：本地索引文档构建 + 分组排序 + 摘要抽取。
 *
 * 索引复用既有 REST 数据（章节标题+正文、角色/线索/伏笔账本），无新端点；
 * 排序规则：组序固定「章节 → 角色 → 线索 → 伏笔」，组内按命中档位
 * （0 精确名 / 1 名含 / 2 字段含）升序、同名按中文 locale 排序，每组封顶。
 */

import { chapterNumber, parseChapterHeading } from "./chapter";

export type SearchKind = "character" | "chapter" | "clue" | "foreshadow";

/** 组序固定（下拉分组展示顺序）。 */
export const SEARCH_GROUP_ORDER: SearchKind[] = ["chapter", "character", "clue", "foreshadow"];

/** 组名中文标签。 */
export const SEARCH_GROUP_LABELS: Record<SearchKind, string> = {
  chapter: "章节",
  character: "角色",
  clue: "线索",
  foreshadow: "伏笔",
};

/** 一条可检索文档：name 展示名，haystack 为名称+关键字段拼合的检索面。 */
export interface SearchDoc {
  kind: SearchKind;
  name: string;
  haystack: string;
  /** kind=chapter 时定位章节的 id。 */
  chapterId?: string;
}

/** 由章节正文构建文档：名称为「第N章 标题」；无标题章回落章号，非 chNN 命名回落 id。 */
export function buildChapterDoc(id: string, content: string): SearchDoc {
  const parsed = parseChapterHeading(content);
  const title = parsed.title ?? "";
  // 章号前缀只加给 chNN 形态（ch03 → 第3章）；非 chNN（如「序章」）直接以标题/id 为名。
  const prefix = /^ch\d+$/i.test(id) ? `第${chapterNumber(id)}章` : id;
  return {
    kind: "chapter",
    chapterId: id,
    name: title === "" ? prefix : `${prefix} ${title}`,
    haystack: [parsed.title ?? "", parsed.body].filter((s) => s !== "").join("\n"),
  };
}

/** 账本实体非 name 字段的检索面文本：数组顿号连接，空值跳过，布尔不参与检索。 */
function fieldText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(String).join("、");
  if (typeof value === "boolean") return "";
  return String(value);
}

/** 由一类账本实体构建文档：name 进展示名，其余字符串字段拼进 haystack。 */
export function buildLedgerDocs(
  kind: Extract<SearchKind, "character" | "clue" | "foreshadow">,
  entities: Record<string, unknown>[],
): SearchDoc[] {
  return entities.map((entity) => {
    const name = String(entity["name"] ?? "");
    const fields = Object.entries(entity)
      .filter(([k]) => k !== "name")
      .map(([, v]) => fieldText(v))
      .filter((s) => s !== "");
    return { kind, name, haystack: [name, ...fields].filter((s) => s !== "").join(" ") };
  });
}

/** 命中档位：0 精确名 / 1 名含 / 2 字段含（-1 无命中）。 */
export type SearchTier = 0 | 1 | 2;

export interface SearchHit {
  doc: SearchDoc;
  tier: SearchTier;
  /** 命中上下文摘要（约 40 字窗口，首尾省略号）。 */
  snippet: string;
}

/** 从检索面截取命中窗口：换行归一化，超窗首尾省略号。 */
function snippetOf(haystack: string, q: string): string {
  const idx = haystack.toLowerCase().indexOf(q);
  if (idx < 0) return "";
  const start = Math.max(0, idx - 16);
  const end = Math.min(haystack.length, idx + q.length + 24);
  const prefix = start > 0 ? "…" : "";
  const suffix = end < haystack.length ? "…" : "";
  const core = haystack.slice(start, end).replace(/\s+/g, " ").trim();
  return `${prefix}${core}${suffix}`;
}

/**
 * 检索：空查询 → 空结果（下拉不展示）；组序固定，组内按档位/名称排序，每组封顶。
 * 大小写不敏感（英文名场景）。返回已按展示顺序拍平的结果列表。
 */
export function searchIndex(docs: SearchDoc[], query: string, perGroup = 6): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [];
  const byKind: Record<SearchKind, SearchHit[]> = {
    chapter: [],
    character: [],
    clue: [],
    foreshadow: [],
  };
  for (const doc of docs) {
    const name = doc.name.toLowerCase();
    const tier =
      name === q ? 0 : name.includes(q) ? 1 : doc.haystack.toLowerCase().includes(q) ? 2 : null;
    if (tier === null) continue;
    byKind[doc.kind].push({ doc, tier, snippet: snippetOf(doc.haystack, q) });
  }
  const out: SearchHit[] = [];
  for (const kind of SEARCH_GROUP_ORDER) {
    const hits = byKind[kind]
      .sort((a, b) => a.tier - b.tier || a.doc.name.localeCompare(b.doc.name, "zh"))
      .slice(0, perGroup);
    out.push(...hits);
  }
  return out;
}
