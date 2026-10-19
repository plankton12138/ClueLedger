/**
 * 图谱文字目录（V03-6.5）纯函数测试。
 *
 * 关键不变量（计划 M12、验收 A22）：
 * - 文字目录与画布读同一份只读投影，标签口径一致（语义层/线索状态/证据状态）；
 * - 键盘能在目录里逐行移动并选中（方向键、Home/End，到端点即停）；
 * - 目录/画布是查看方式偏好，按 project + view 隔离，存储不可用时不抛错。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClueStateWire, GraphEdgeWire, GraphNodeWire } from "../api/graphs";
import {
  buildDirectory,
  flattenRows,
  graphEmptyText,
  graphModeKey,
  navigateRows,
  readGraphMode,
  rowAtIndex,
  rowDomId,
  rowKey,
  writeGraphMode,
} from "./graphDirectory";

function node(overrides: Partial<GraphNodeWire> & { id: string }): GraphNodeWire {
  return {
    kind: "character",
    kind_label: "人物",
    title: "",
    summary: "",
    revision: "rev-1",
    semantic_layer: "truth",
    evidence_status: "not_recorded",
    source_refs: [],
    ...overrides,
  };
}

function edge(overrides: Partial<GraphEdgeWire> & { id: string; source: string; target: string }): GraphEdgeWire {
  return {
    label: "",
    relation_type: "relationship",
    semantic_layer: "knowledge",
    evidence_status: "not_recorded",
    declared_by: null,
    source_refs: [],
    ...overrides,
  };
}

function clue(overrides: Partial<ClueStateWire> & { clue_id: string }): ClueStateWire {
  return {
    statement: "",
    necessity: false,
    points_to: [],
    planned_beats: [],
    disclosed_beats: [],
    status: "missing",
    first_position: null,
    within_position: null,
    ...overrides,
  };
}

const NODES: GraphNodeWire[] = [
  node({ id: "art_sis", title: "姐姐", summary: "与妹妹同住" }),
  node({ id: "art_bro", title: "弟弟", summary: "常年在外" }),
  node({ id: "clue_watch", kind: "clue", kind_label: "线索", title: "停走的怀表", semantic_layer: "disclosure" }),
];

const EDGES: GraphEdgeWire[] = [
  edge({
    id: "rel_1",
    source: "art_sis",
    target: "art_bro",
    label: "兄妹",
    relation_type: "kinship",
    public_state: "关系融洽",
    true_state: "姐姐隐瞒了怀表去向",
  }),
  edge({
    id: "rel_2",
    source: "clue_watch",
    target: "art_sis",
    label: "指向",
    relation_type: "clue_points_to",
    evidence_status: "needs_check",
  }),
];

describe("buildDirectory：分组、顺序与关联归属", () => {
  it("按种类分组、组内按标题排序，组也按标签排序", () => {
    const groups = buildDirectory({ nodes: NODES, edges: EDGES });
    expect(groups.map((group) => group.label)).toEqual(["人物", "线索"]);
    const characterTitles = groups[0].rows
      .filter((row) => row.kind === "node")
      .map((row) => row.title);
    // 拼音排序：「弟」(di) 在「姐」(jie) 之前
    expect(characterTitles).toEqual(["弟弟", "姐姐"]);
  });

  it("关联只挂在起点条目下，每条只出现一次", () => {
    const rows = flattenRows(buildDirectory({ nodes: NODES, edges: EDGES }));
    const edgeRows = rows.filter((row) => row.kind === "edge");
    expect(edgeRows.map((row) => row.id).sort()).toEqual(["rel_1", "rel_2"]);
    // rel_1 起点是「姐姐」，出现在人物组里紧跟其条目；rel_2 起点是怀表
    const source = rows.findIndex((row) => row.key === rowKey("node", "art_sis"));
    expect(rows[source + 1].key).toBe(rowKey("edge", "rel_1"));
  });

  it("条目行带语义层无关的摘要与证据状态；线索条目改用陈述与状态", () => {
    const groups = buildDirectory({
      nodes: NODES,
      edges: EDGES,
      clues: [clue({ clue_id: "clue_watch", statement: "怀表早已损坏", necessity: true, status: "planned" })],
    });
    const rows = flattenRows(groups);
    const sister = rowAtIndex(rows, rowKey("node", "art_sis"));
    expect(sister?.detail).toBe("与妹妹同住");
    expect(sister?.badge).toBe("人物");
    expect(sister?.evidenceStatus).toBe("not_recorded");

    const watch = rowAtIndex(rows, rowKey("node", "clue_watch"));
    expect(watch?.detail).toBe("怀表早已损坏");
    expect(watch?.note).toBe("计划出现，尚未写入正文 · 必要线索");
    expect(watch?.badge).toBe("线索");
  });

  it("关系行写明两端与表面/真实关系，角色视角隐藏真实关系时如实标注", () => {
    const rows = flattenRows(buildDirectory({ nodes: NODES, edges: EDGES }));
    const rel = rowAtIndex(rows, rowKey("edge", "rel_1"));
    expect(rel?.title).toBe("兄妹");
    expect(rel?.badge).toBe("kinship");
    expect(rel?.detail).toBe("姐姐 → 弟弟 · 表面关系：关系融洽 · 真实关系：姐姐隐瞒了怀表去向");
    expect(rel?.note).toBe("语义层：认知");
    expect(rel?.depth).toBe(1);

    const hidden = flattenRows(
      buildDirectory({
        nodes: NODES,
        edges: [edge({ id: "rel_3", source: "art_sis", target: "art_bro", relation_type: "kinship", true_state_hidden: true })],
      }),
    );
    expect(rowAtIndex(hidden, rowKey("edge", "rel_3"))?.detail).toBe(
      "姐姐 → 弟弟 · 真实关系：当前视角不可见",
    );
  });

  it("空图谱得到空目录", () => {
    expect(buildDirectory({ nodes: [], edges: [] })).toEqual([]);
    expect(flattenRows([])).toEqual([]);
  });
});

describe("键盘移动：方向键、Home/End、到端点即停", () => {
  const rows = flattenRows(buildDirectory({ nodes: NODES, edges: EDGES }));
  const keys = rows.map((row) => row.key);

  it("向下、向上逐行移动", () => {
    expect(navigateRows(rows, keys[0], "ArrowDown")).toBe(keys[1]);
    expect(navigateRows(rows, keys[2], "ArrowUp")).toBe(keys[1]);
  });

  it("首行再向上、末行再向下都停在端点，不绕回另一端", () => {
    expect(navigateRows(rows, keys[0], "ArrowUp")).toBe(keys[0]);
    expect(navigateRows(rows, keys[keys.length - 1], "ArrowDown")).toBe(keys[keys.length - 1]);
  });

  it("Home/End 直达首末行", () => {
    expect(navigateRows(rows, keys[1], "Home")).toBe(keys[0]);
    expect(navigateRows(rows, keys[1], "End")).toBe(keys[keys.length - 1]);
  });

  it("无当前行时向下落到首行、向上落到末行；空目录与其他键返回 null", () => {
    expect(navigateRows(rows, null, "ArrowDown")).toBe(keys[0]);
    expect(navigateRows(rows, null, "ArrowUp")).toBe(keys[keys.length - 1]);
    expect(navigateRows(rows, "node:missing", "ArrowDown")).toBe(keys[0]);
    expect(navigateRows(rows, keys[0], "Enter")).toBeNull();
    expect(navigateRows([], null, "ArrowDown")).toBeNull();
  });

  it("行键与 DOM id 口径稳定，选中行可被查回", () => {
    expect(rowKey("node", "art_sis")).toBe("node:art_sis");
    expect(rowKey("edge", "rel_1")).toBe("edge:rel_1");
    expect(rowDomId("node:art_sis")).toBe("graph-row-node:art_sis");
    expect(rowAtIndex(rows, rowKey("node", "art_bro"))?.title).toBe("弟弟");
    expect(rowAtIndex(rows, null)).toBeNull();
    expect(rowAtIndex(rows, "node:missing")).toBeNull();
  });
});

describe("查看方式偏好：按 project + view 隔离，存储不可用不抛错", () => {
  function makeStorage(): Map<string, string> {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    });
    return store;
  }

  let storage: Map<string, string>;

  beforeEach(() => {
    storage = makeStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("缺省为画布；写目录后读回，换作品/换视图互不影响", () => {
    expect(readGraphMode("prj_1", "characters")).toBe("canvas");
    writeGraphMode("prj_1", "characters", "directory");
    expect(readGraphMode("prj_1", "characters")).toBe("directory");
    expect(readGraphMode("prj_1", "clues")).toBe("canvas");
    expect(readGraphMode("prj_2", "characters")).toBe("canvas");
    expect(storage.get(graphModeKey("prj_1", "characters"))).toBe("directory");
  });

  it("存储不可用时读回画布，写入不抛错", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(readGraphMode("prj_1", "characters")).toBe("canvas");
    expect(() => writeGraphMode("prj_1", "characters", "directory")).not.toThrow();
  });
});

describe("空图说明（V03-7.5 / 7.6）", () => {
  it("手法结构给「先建真相条目」的下一步，而不是空白", () => {
    expect(graphEmptyText("method")).toContain("真相");
    expect(graphEmptyText("method")).toContain("手法");
    expect(graphEmptyText("characters")).toContain("人物");
    expect(graphEmptyText("clues")).toContain("线索");
  });

  it("时间线给「先登记事件」的下一步", () => {
    expect(graphEmptyText("timeline")).toContain("时间线事件");
    expect(graphEmptyText("timeline")).toContain("分轨");
  });
});
