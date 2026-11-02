/**
 * 构思页状态：只放「看哪个方案、选中哪个节点、写入后按服务端修订重读」。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listStoryVariants: vi.fn(),
  readStoryVariant: vi.fn(),
  undoStoryChange: vi.fn(),
}));

vi.mock("../api/story", async () => {
  const actual = await vi.importActual<typeof import("../api/story")>("../api/story");
  return { ...actual, ...api };
});

import { useStoryStore } from "./story";

const VARIANT = {
  id: "main",
  name: "主方案",
  revision: "rev_1",
  primary: true,
  base_variant_id: null,
  base_revision: null,
  counts: { stages: 0, nodes: 2, clues: 1, threads: 0, constraints: 0, edges: 1, bindings: 0 },
};


const GRAPH = {
  variant_id: "main",
  name: "主方案",
  revision: "rev_1",
  stages: [],
  nodes: [
    { id: "n1", title: "抵达", action: "走进旅馆", change: null, stage_id: null, thread_ids: [], clue_ids: ["c1"], time_note: "" },
    { id: "n2", title: "对质", action: "他问管家", change: null, stage_id: null, thread_ids: [], clue_ids: [], time_note: "" },
  ],
  edges: [{ id: "e1", from_id: "n1", to_id: "n2", kind: "next" as const, reason: "" }],
  threads: [],
  event_times: [],
  constraints: [],
  collapsed: [],
};

function reset(): void {
  useStoryStore.getState().reset();
  vi.clearAllMocks();
  api.listStoryVariants.mockResolvedValue({
    revision: "doc_1",
    primary_variant_id: "main",
    variants: [VARIANT],
  });
  api.readStoryVariant.mockResolvedValue(GRAPH);
}

describe("构思状态", () => {
  beforeEach(reset);

  it("打开时选中主方案并读取图", async () => {
    await useStoryStore.getState().load("w1");

    const state = useStoryStore.getState();
    expect(state.variantId).toBe("main");
    expect(state.graph?.nodes).toHaveLength(2);
    expect(api.readStoryVariant).toHaveBeenCalledWith("w1", "main");
  });

  it("同一本再次加载后，首次迟到的失败不覆盖新图", async () => {
    let reject: (error: Error) => void = () => undefined;
    api.listStoryVariants.mockImplementationOnce(() => new Promise((_, no) => { reject = no; }));
    const old = useStoryStore.getState().load("w1");
    await useStoryStore.getState().load("w1");
    reject(new Error("旧请求失败"));
    await old;
    expect(useStoryStore.getState().graph).toEqual(GRAPH);
    expect(useStoryStore.getState().error).toBeNull();
  });

  it("切换作品会清空当前节点选择", async () => {
    await useStoryStore.getState().load("w1");
    useStoryStore.getState().selectNode("n1");
    expect(useStoryStore.getState().nodeId).toBe("n1");

    await useStoryStore.getState().load("w2");
    expect(useStoryStore.getState().nodeId).toBeNull();
  });

  it("节点选择可清除，只保留节点标识供左栏过滤", async () => {
    await useStoryStore.getState().load("w1");

    useStoryStore.getState().selectNode("n1");
    expect(useStoryStore.getState().nodeId).toBe("n1");

    useStoryStore.getState().selectNode(null);
    expect(useStoryStore.getState().nodeId).toBeNull();
  });

  it("结构写入后的失效序号递增，详情按它重读", async () => {
    await useStoryStore.getState().load("w1");
    const before = useStoryStore.getState().invalidation;

    useStoryStore.getState().invalidate();

    expect(useStoryStore.getState().invalidation).toBe(before + 1);
  });

  it("撤销上一次改动后清掉可撤销标记", async () => {
    await useStoryStore.getState().load("w1");
    useStoryStore.setState({
      lastChange: {
        change_id: "ui_1",
        variant_id: "main",
        base_revision: "rev_1",
        revision: "rev_2",
        created_at: "",
        operations: [],
        changed: [],
        undone_by: null,
      },
    });
    api.undoStoryChange.mockResolvedValue({});

    const ok = await useStoryStore.getState().undo();

    expect(ok).toBe(true);
    expect(api.undoStoryChange).toHaveBeenCalledWith("w1", "ui_1", expect.any(String));
    expect(useStoryStore.getState().lastChange).toBeNull();
  });

  it("模型保存后刷新空白构思，不重置当前浏览方案", async () => {
    api.listStoryVariants.mockResolvedValueOnce({ revision: "empty", primary_variant_id: "", variants: [] });
    await useStoryStore.getState().load("w1");
    await useStoryStore.getState().refresh("w1");
    expect(useStoryStore.getState().graph?.nodes).toHaveLength(2);
    expect(useStoryStore.getState().variantId).toBe("main");
    useStoryStore.getState().selectNode("n1");
    await useStoryStore.getState().refresh("w1");
    expect(useStoryStore.getState().nodeId).toBe("n1");
  });

  it("线索选择是构思页共用状态，切换作品后清空", async () => {
    await useStoryStore.getState().load("w1");
    useStoryStore.getState().selectClue("c1");
    expect(useStoryStore.getState().clueId).toBe("c1");
    await useStoryStore.getState().load("w2");
    expect(useStoryStore.getState().clueId).toBeNull();
  });

});

describe("构思刷新并发", () => {
  beforeEach(reset);

  it("并发刷新只采用最新结果，旧快照不覆盖新修订", async () => {
    await useStoryStore.getState().load("w1");
    let resolveOld: (value: unknown) => void = () => undefined;
    let resolveNew: (value: unknown) => void = () => undefined;
    api.readStoryVariant
      .mockImplementationOnce(() => new Promise((done) => { resolveOld = done; }))
      .mockImplementationOnce(() => new Promise((done) => { resolveNew = done; }));

    const older = useStoryStore.getState().refresh("w1");
    const newer = useStoryStore.getState().refresh("w1");
    await Promise.resolve();
    resolveNew({ ...GRAPH, revision: "rev_new" });
    await newer;
    resolveOld({ ...GRAPH, revision: "rev_old" });
    await older;

    expect(useStoryStore.getState().graph?.revision).toBe("rev_new");
  });
});
