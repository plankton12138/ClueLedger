/**
 * 图谱布局偏好与引用文案（V03-6.4）纯函数测试。
 *
 * 关键不变量（计划 M12、产品 §5.8）：
 * - 拖动只写布局偏好（localStorage），不触发任何领域写入（无 fetch）；
 * - 布局按 project + view 隔离，换作品/换视图互不污染；
 * - 初始布局确定性：同一批节点得到同一批坐标，位置不表示重要性；
 * - 「加入讨论」的引用文案由纯函数构建，可附到主输入框。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  appendReference,
  buildDiscussionReference,
  dragNode,
  graphLayoutKey,
  initialLayout,
  readLayout,
  readSelection,
  resolveLayout,
  settledPositions,
  writeLayout,
  writeSelection,
} from "./graphLayout";

/** 最小 localStorage 替身：node 测试环境没有浏览器存储。 */
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

describe("初始布局：确定性、位置不表示重要性", () => {
  it("同一批节点得到同一批坐标（按 ID 排序，与输入顺序无关）", () => {
    const a = initialLayout(["art_c", "art_a", "art_b"]);
    const b = initialLayout(["art_a", "art_b", "art_c"]);
    expect(a).toEqual(b);
    expect(Object.keys(a).sort()).toEqual(["art_a", "art_b", "art_c"]);
  });

  it("单节点落在原点；空集合得到空布局", () => {
    expect(initialLayout(["art_only"])).toEqual({ art_only: { x: 0, y: 0 } });
    expect(initialLayout([])).toEqual({});
  });
});

describe("resolveLayout：已保存位置优先，新节点补初始位置", () => {
  it("保留拖过的坐标，新出现的节点用初始坐标，已消失的节点被丢弃", () => {
    const layout = resolveLayout(["art_a", "art_b"], { art_a: { x: 42, y: 7 } });
    expect(layout.art_a).toEqual({ x: 42, y: 7 });
    expect(layout.art_b).toBeDefined();
    expect(Object.keys(layout).sort()).toEqual(["art_a", "art_b"]);
  });
});

describe("拖动只写布局偏好，零领域写入", () => {
  it("dragNode 返回新布局，原布局不变（纯函数）", () => {
    const before = initialLayout(["art_a", "art_b"]);
    const after = dragNode(before, "art_a", { x: 100, y: 50 });
    expect(after.art_a).toEqual({ x: 100, y: 50 });
    expect(before.art_a).not.toEqual({ x: 100, y: 50 });
    expect(after.art_b).toEqual(before.art_b);
  });

  it("拖动持久化只落 localStorage，绝不发起任何请求", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const layout = dragNode(initialLayout(["art_a"]), "art_a", { x: 12, y: 34 });
    writeLayout("prj_1", "characters", layout);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(JSON.parse(storage.get(graphLayoutKey("prj_1", "characters"))!)).toEqual({
      art_a: { x: 12, y: 34 },
    });
  });
});

describe("大数据初始布局：半径随节点数增长，仍不重叠", () => {
  it("24 个节点两两之间留出可读间距（固定小圆周会把节点挤成一团）", () => {
    const ids = Array.from({ length: 24 }, (_, i) => `art_${String(i).padStart(2, "0")}`);
    const layout = initialLayout(ids);
    let min = Number.POSITIVE_INFINITY;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const dx = layout[ids[i]].x - layout[ids[j]].x;
        const dy = layout[ids[i]].y - layout[ids[j]].y;
        min = Math.min(min, Math.hypot(dx, dy));
      }
    }
    expect(min).toBeGreaterThanOrEqual(150);
  });

  it("极大数据有半径上限，不把画布撑到不可用", () => {
    const ids = Array.from({ length: 400 }, (_, i) => `art_${i}`);
    const layout = initialLayout(ids);
    for (const id of ids) {
      // 坐标取整，给 1px 取整余量
      expect(Math.hypot(layout[id].x, layout[id].y)).toBeLessThanOrEqual(2401);
    }
  });
});

describe("画布位置落盘：只收落定帧，键盘与鼠标共用同一份偏好", () => {
  it("拖动中间帧（dragging: true）不落盘", () => {
    expect(
      settledPositions([
        { id: "art_a", type: "position", position: { x: 1, y: 2 }, dragging: true },
      ]),
    ).toBeNull();
  });

  it("落定帧与键盘移动（dragging: false / 缺省）返回坐标，非位置变更被忽略", () => {
    expect(
      settledPositions([
        { id: "art_a", type: "position", position: { x: 5, y: 6 }, dragging: false },
        { id: "art_b", type: "select", selected: true },
        { id: "art_c", type: "dimensions" },
      ]),
    ).toEqual({ art_a: { x: 5, y: 6 } });
    expect(settledPositions([{ id: "art_a", type: "position", position: { x: 7, y: 8 } }])).toEqual(
      { art_a: { x: 7, y: 8 } },
    );
    expect(settledPositions([{ id: "art_a", type: "position", dragging: true }])).toBeNull();
  });
});

describe("布局按 project + view 隔离", () => {
  it("换作品或换视图读到各自独立的布局", () => {
    writeLayout("prj_1", "characters", { art_a: { x: 1, y: 2 } });
    writeLayout("prj_1", "clues", { art_b: { x: 3, y: 4 } });

    expect(readLayout("prj_1", "characters")).toEqual({ art_a: { x: 1, y: 2 } });
    expect(readLayout("prj_1", "clues")).toEqual({ art_b: { x: 3, y: 4 } });
    expect(readLayout("prj_2", "characters")).toEqual({});
  });

  it("存储不可用时读回空布局，不抛错", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(readLayout("prj_1", "characters")).toEqual({});
    expect(() => writeLayout("prj_1", "characters", { art_a: { x: 0, y: 0 } })).not.toThrow();
  });
});

describe("图谱定位：返回后再打开恢复上次查看的对象", () => {
  it("按 project + view 记录选中对象；清除后读回 null", () => {
    writeSelection("prj_1", "characters", "art_sis");
    expect(readSelection("prj_1", "characters")).toBe("art_sis");
    // 换视图/换作品互不污染
    expect(readSelection("prj_1", "clues")).toBeNull();
    expect(readSelection("prj_2", "characters")).toBeNull();

    writeSelection("prj_1", "characters", null);
    expect(readSelection("prj_1", "characters")).toBeNull();
  });
});

describe("加入讨论：引用文案构建", () => {
  it("包含种类、标题与版本（版本截断到 8 位）", () => {
    const text = buildDiscussionReference({
      kindLabel: "人物",
      title: "姐姐",
      id: "art_sis",
      revision: "abcdef1234567890",
    });
    expect(text).toBe("参考人物「姐姐」（版本 abcdef12）");
  });

  it("带摘要时附上说明；无版本时省略版本段", () => {
    const text = buildDiscussionReference({
      kindLabel: "线索",
      title: "停走的怀表",
      id: "art_watch",
      detail: "指向「怀表早已损坏」",
    });
    expect(text).toBe("参考线索「停走的怀表」：指向「怀表早已损坏」");
  });

  it("appendReference 把引用接到草稿（空草稿直接落地，非空另起一行）", () => {
    expect(appendReference("", "参考线索「怀表」")).toBe("参考线索「怀表」");
    expect(appendReference("现有草稿", "参考线索「怀表」")).toBe(
      "现有草稿\n参考线索「怀表」",
    );
    expect(appendReference("现有草稿\n", "参考线索「怀表」")).toBe(
      "现有草稿\n参考线索「怀表」",
    );
  });
});
