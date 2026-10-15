/**
 * 角色关系解析（P6 T10，计划 D8）：纯函数，可测。
 *
 * 数据约定：`Character.relationships` 是自由文本列表，逐条匹配
 * `「<已注册角色名>[:：]<关系描述>」` 模式 → 边；解析不出 / 指向未注册
 * 角色的条目不硬解析、不静默丢——进 ``unconnected`` 原样列出（泄压阀），
 * 关系图下方展示，并提示补齐。
 */

export interface RelationInput {
  name: string;
  relationships: string[];
}

export interface ParsedRelation {
  target: string;
  description: string;
}

/** 单条关系文本解析：`目标名[:：]描述`；不匹配返回 null。 */
export function parseRelation(entry: string): ParsedRelation | null {
  const match = /^\s*(.+?)\s*[:：]\s*(.*)$/.exec(entry);
  if (match === null) return null;
  const target = match[1]?.trim() ?? "";
  const description = match[2]?.trim() ?? "";
  if (target === "") return null;
  return { target, description };
}

export interface RelationEdge {
  source: string;
  target: string;
  label: string;
}

export interface RelationUnconnected {
  source: string;
  entry: string;
}

export interface RelationGraphData {
  /** 节点：注册角色全量（关系图只画有边的节点，无边的进 unconnected 提示）。 */
  nodes: { id: string }[];
  edges: RelationEdge[];
  unconnected: RelationUnconnected[];
}

/**
 * 由角色账本构建关系图数据：目标名必须是已注册角色才成边；
 * 其余（不可解析 / 指向未知角色）进 unconnected 原样保留。
 */
export function buildGraphData(inputs: RelationInput[]): RelationGraphData {
  const names = new Set(inputs.map((n) => n.name));
  const edges: RelationEdge[] = [];
  const unconnected: RelationUnconnected[] = [];
  for (const input of inputs) {
    for (const entry of input.relationships) {
      const parsed = parseRelation(entry);
      if (parsed !== null && names.has(parsed.target)) {
        edges.push({ source: input.name, target: parsed.target, label: parsed.description });
      } else {
        unconnected.push({ source: input.name, entry });
      }
    }
  }
  return { nodes: inputs.map((n) => ({ id: n.name })), edges, unconnected };
}
