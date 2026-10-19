/**
 * 图谱文字目录（V03-6.5）——纯函数，让「不用画布」也能查看条目、来源并继续写作。
 *
 * 设计依据（产品 §5.4/§5.8、计划 M12、验收 A22）：
 * - 画布只是投影，不是唯一入口。同一份只读投影同时给出**文字目录**：种类分组 → 条目
 *   （标题、语义层、证据状态、版本）→ 该条目的关联，键盘可逐行移动并选中，选中后与画布
 *   共用同一个详情区（查看来源、定位正文、加入讨论）。
 * - 键盘与画布具备相同能力：方向键移动、Home/End 首尾、回车/空格选中；移动只是查看位置，
 *   不写任何领域数据。
 * - 目录/画布是**查看方式偏好**，按 project + view 记在浏览器本地（localStorage），
 *   与布局偏好同属本地区，不落服务端。
 */

import { clueStatusLabel, layerLabel, relationSummary } from "./graphLabels";
import type { ClueStateWire, GraphEdgeWire, GraphNodeWire } from "../api/graphs";

export type GraphMode = "canvas" | "directory";

export interface DirectoryRow {
  /** 行键：`node:<id>` 或 `edge:<id>`，用于键盘定位与 aria-activedescendant。 */
  key: string;
  kind: "node" | "edge";
  id: string;
  /** 0 为条目本身，1 为条目的关联（缩进显示）。 */
  depth: 0 | 1;
  title: string;
  /** 种类（人物/线索）或关系类型标签。 */
  badge: string;
  /** 摘要、线索陈述或关系概述。 */
  detail: string;
  /** 附加说明行（如线索状态），无则空串。 */
  note: string;
  evidenceStatus: string;
}

export interface DirectoryGroup {
  key: string;
  label: string;
  rows: DirectoryRow[];
}

/** 空图说明：无对象时给「下一步做什么」，而不是留下一片空白。画布与文字目录共用。 */
export function graphEmptyText(viewId: string): string {
  if (viewId === "characters") {
    return "还没有可画的人物关系。先在账本登记人物与关系，图中才会出现节点。";
  }
  if (viewId === "method") {
    return "还没有可画的手法结构。先在账本新建「真相」条目并填写凶手、手法、动机与掩盖方式。";
  }
  if (viewId === "timeline") {
    return "还没有可分轨的事件。先在账本登记时间线事件及参与者或地点。";
  }
  return "还没有可画的线索关联。先在账本登记线索及其指向。";
}

/** 行键：画布选中与目录选中共用同一个 key 口径。 */
export function rowKey(kind: "node" | "edge", id: string): string {
  return `${kind}:${id}`;
}

/** 行 DOM id：aria-activedescendant 与滚动到可见都按它定位。 */
export function rowDomId(key: string): string {
  return `graph-row-${key}`;
}

/** 目录内排序按中文习惯（拼音）：作者按读音找条目，比按码位排更接近翻目录的直觉。 */
function compareText(a: string, b: string): number {
  return a.localeCompare(b, "zh-CN");
}

function nodeRow(node: GraphNodeWire, clue: ClueStateWire | null): DirectoryRow {
  const noteParts = clue
    ? [clueStatusLabel(clue.status), clue.necessity ? "必要线索" : ""].filter(Boolean)
    : [];
  return {
    key: rowKey("node", node.id),
    kind: "node",
    id: node.id,
    depth: 0,
    title: node.title || node.id,
    badge: node.kind_label || node.kind,
    detail: clue?.statement || node.summary,
    note: noteParts.join(" · "),
    evidenceStatus: node.evidence_status,
  };
}

function edgeRow(
  edge: GraphEdgeWire,
  nodesById: Record<string, GraphNodeWire>,
): DirectoryRow {
  return {
    key: rowKey("edge", edge.id),
    kind: "edge",
    id: edge.id,
    depth: 1,
    title: edge.label || edge.relation_type,
    badge: edge.relation_type,
    detail: relationSummary(edge, nodesById),
    note: `语义层：${layerLabel(edge.semantic_layer)}`,
    evidenceStatus: edge.evidence_status,
  };
}

/**
 * 构建文字目录：条目按种类分组，组内条目后跟该条目对外指向的关联。
 * 每条关联只出现在其**起点**条目下，避免同一关系在目录里出现两次。
 */
export function buildDirectory(input: {
  nodes: readonly GraphNodeWire[];
  edges: readonly GraphEdgeWire[];
  clues?: readonly ClueStateWire[];
}): DirectoryGroup[] {
  const nodesById = Object.fromEntries(input.nodes.map((node) => [node.id, node]));
  const clueById = Object.fromEntries((input.clues ?? []).map((clue) => [clue.clue_id, clue]));

  const byKind = new Map<string, GraphNodeWire[]>();
  for (const node of input.nodes) {
    const label = node.kind_label || node.kind;
    const bucket = byKind.get(label);
    if (bucket) bucket.push(node);
    else byKind.set(label, [node]);
  }

  const groups: DirectoryGroup[] = [];
  for (const [label, kindNodes] of byKind) {
    const sorted = [...kindNodes].sort((a, b) =>
      compareText(a.title || a.id, b.title || b.id),
    );
    const rows: DirectoryRow[] = [];
    for (const node of sorted) {
      rows.push(nodeRow(node, clueById[node.id] ?? null));
      const outgoing = input.edges
        .filter((edge) => edge.source === node.id)
        .sort((a, b) => compareText(a.label || a.relation_type, b.label || b.relation_type));
      for (const edge of outgoing) rows.push(edgeRow(edge, nodesById));
    }
    groups.push({ key: label, label, rows });
  }
  return groups.sort((a, b) => compareText(a.label, b.label));
}

/** 展平为一个有序键盘序列：组的顺序即行的顺序。 */
export function flattenRows(groups: readonly DirectoryGroup[]): DirectoryRow[] {
  return groups.flatMap((group) => group.rows);
}

/**
 * 键盘移动：方向键上下、Home/End；到端点即停（不回卷，避免长列表里「按一下就绕回开头」）。
 * 当前行不在列表（或被清空）时，向下/Home 落到首行，向上/End 落到末行。
 */
export function navigateRows(
  rows: readonly DirectoryRow[],
  currentKey: string | null,
  pressed: string,
): string | null {
  if (rows.length === 0) return null;
  const index = currentKey === null ? -1 : rows.findIndex((row) => row.key === currentKey);
  switch (pressed) {
    case "ArrowDown":
      return rows[index < 0 ? 0 : Math.min(index + 1, rows.length - 1)].key;
    case "ArrowUp":
      return rows[index < 0 ? rows.length - 1 : Math.max(index - 1, 0)].key;
    case "Home":
      return rows[0].key;
    case "End":
      return rows[rows.length - 1].key;
    default:
      return null;
  }
}

export function rowAtIndex(
  rows: readonly DirectoryRow[],
  key: string | null,
): DirectoryRow | null {
  if (key === null) return null;
  return rows.find((row) => row.key === key) ?? null;
}

const MODE_PREFIX = "hastings.graph.mode";

/** 查看方式偏好键：按 project + view 隔离。 */
export function graphModeKey(projectId: string, viewId: string): string {
  return `${MODE_PREFIX}.${projectId}.${viewId}`;
}

/** 读取查看方式；无记录或存储不可用回退画布（缺省视图）。 */
export function readGraphMode(projectId: string, viewId: string): GraphMode {
  try {
    return localStorage.getItem(graphModeKey(projectId, viewId)) === "directory"
      ? "directory"
      : "canvas";
  } catch {
    return "canvas";
  }
}

/** 写入查看方式；存储不可用时只本次会话生效。 */
export function writeGraphMode(projectId: string, viewId: string, mode: GraphMode): void {
  try {
    localStorage.setItem(graphModeKey(projectId, viewId), mode);
  } catch {
    /* 禁用存储时仅本次会话生效。 */
  }
}
