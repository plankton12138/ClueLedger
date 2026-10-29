/**
 * 构思页状态：只放「看哪个方案、选中哪个节点、写入后按服务端修订重读」。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listStoryVariants: vi.fn(),
  readStoryVariant: vi.fn(),
  readStoryNode: vi.fn(),
  submitStoryChange: vi.fn(),
  createStoryVariant: vi.fn(),
  compareStoryVariants: vi.fn(),
  adoptStoryVariant: vi.fn(),
  undoStoryChange: vi.fn(),
}));

vi.mock("../api/story", async () => {
  const actual = await vi.importActual<typeof import("../api/story")>("../api/story");
  return { ...actual, ...api };
});

import { useStoryStore } from "./story";
import { WorkspaceApiError } from "../api/workspace";

const VARIANT = {
  id: "main",
  name: "主方案",
  revision: "rev_1",
  primary: true,
  base_variant_id: null,
  base_revision: null,
  counts: { nodes: 2, clues: 1, threads: 0, constraints: 0, edges: 1, bindings: 0 },
};

const ALT = { ...VARIANT, id: "variant_alt", name: "备选", revision: "rev_alt", primary: false };

const GRAPH = {
  variant_id: "main",
  name: "主方案",
  revision: "rev_1",
  nodes: [
    { id: "n1", title: "抵达", action: "走进旅馆", threads: [], clues: ["c1"], time_note: "", group_id: null },
    { id: "n2", title: "对质", action: "他问管家", threads: [], clues: [], time_note: "", group_id: null },
  ],
  edges: [{ id: "e1", from_id: "n1", to_id: "n2", kind: "next" as const, reason: "" }],
  threads: [],
  event_times: [],
  constraints: [],
  collapsed: [],
};

const DETAIL = {
  variant_id: "main",
  revision: "rev_1",
  node: GRAPH.nodes[0],
  clue_uses: [
    {
      clue_id: "c1",
      title: "手套",
      author_truth: "管家搬过尸体",
      intention: "",
      presentation: "手套还在滴水",
      mode: "narration",
      speaker_id: "",
      expected_interpretation: "",
    },
  ],
  knowledge_changes: [],
  bindings: [],
  incoming: [],
  outgoing: GRAPH.edges,
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
  api.readStoryNode.mockResolvedValue(DETAIL);
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

  it("选中节点读详情；切走后迟到的详情不覆盖", async () => {
    await useStoryStore.getState().load("w1");
    let resolve: (value: unknown) => void = () => undefined;
    api.readStoryNode.mockImplementationOnce(
      () => new Promise((done) => { resolve = done; }),
    );

    void useStoryStore.getState().selectNode("n1");
    await useStoryStore.getState().selectNode(null);
    resolve(DETAIL);

    expect(useStoryStore.getState().nodeId).toBeNull();
    expect(useStoryStore.getState().detail).toBeNull();
  });

  it("提交变更用当前修订，成功后按服务端结果重读", async () => {
    await useStoryStore.getState().load("w1");
    api.submitStoryChange.mockResolvedValue({
      variant_id: "main",
      base_revision: "rev_1",
      revision: "rev_2",
      change_id: "ui_1",
      request_key: "k1",
      result: {},
      changed: ["node:n3"],
    });
    api.readStoryVariant.mockResolvedValue({ ...GRAPH, revision: "rev_2" });

    const ok = await useStoryStore.getState().submit(
      [{ op: "add_node", node: { id: "n3", title: "雪停", action: "雪停了" } }],
      "加一个节点",
    );

    expect(ok).toBe(true);
    expect(api.submitStoryChange).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({ variant_id: "main", expected_revision: "rev_1", note: "加一个节点" }),
    );
    expect(useStoryStore.getState().graph?.revision).toBe("rev_2");
    expect(useStoryStore.getState().lastChange?.change_id).toBe("ui_1");
  });

  it("修订冲突时保留原图并给出错误", async () => {
    await useStoryStore.getState().load("w1");
    api.submitStoryChange.mockRejectedValue(
      new WorkspaceApiError("STORY_REVISION_CONFLICT", "方案已经更新", "刷新后重试", 409, "story"),
    );

    const ok = await useStoryStore.getState().submit([{ op: "add_node" }]);

    expect(ok).toBe(false);
    expect(useStoryStore.getState().graph?.revision).toBe("rev_1");
    expect(useStoryStore.getState().error).toContain("方案已经更新");
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

  it("片段折叠保留片段列表，显示被折叠的节点数", async () => {
    const groupGraph = {
      ...GRAPH,
      nodes: GRAPH.nodes.map((node, index) => ({ ...node, group_id: index === 0 ? "开端" : null })),
    };
    api.readStoryVariant.mockResolvedValue(groupGraph);
    await useStoryStore.getState().load("w1");
    expect(useStoryStore.getState().groups).toEqual(["开端"]);

    const folded = { ...groupGraph, nodes: [groupGraph.nodes[1]], edges: [], collapsed: ["n1"] };
    api.readStoryVariant.mockResolvedValue(folded);
    await useStoryStore.getState().fold("对质");

    const state = useStoryStore.getState();
    expect(state.groupId).toBe("对质");
    expect(state.groups).toEqual(["开端"]);
    expect(state.graph?.collapsed).toEqual(["n1"]);
  });

  it("引用到对话登记当前方案的节点，清除后不再带着", async () => {
    await useStoryStore.getState().load("w1");

    useStoryStore.getState().referenceNode("n1");
    expect(useStoryStore.getState().reference).toMatchObject({
      variantId: "main",
      revision: "rev_1",
      nodeIds: ["n1"],
      title: "抵达",
    });

    useStoryStore.getState().clearReference();
    expect(useStoryStore.getState().reference).toBeNull();
  });

  it("采用把备选结构复制到主方案，冲突时保留服务端的逐项原因", async () => {
    api.listStoryVariants.mockResolvedValue({
      revision: "doc_1",
      primary_variant_id: "main",
      variants: [VARIANT, ALT],
    });
    await useStoryStore.getState().load("w1");
    await useStoryStore.getState().selectVariant("variant_alt");
    api.adoptStoryVariant.mockResolvedValue({
      plan: {
        source: "variant_alt",
        target: "main",
        node_ids: ["n3"],
        copied: { nodes: ["n3"], clues: [], threads: [], constraints: [] },
        unchanged: [],
        pulled_in: [],
        skipped_edges: [],
        conflicts: [],
        operations: 1,
        note: "",
      },
      receipt: {
        variant_id: "main",
        base_revision: "rev_1",
        revision: "rev_2",
        change_id: "c1",
        request_key: "k",
        result: {},
        changed: ["node:n3"],
      },
    });
    api.readStoryVariant.mockResolvedValue({ ...GRAPH, variant_id: "variant_alt" });

    const ok = await useStoryStore.getState().adopt(["n3"]);

    expect(ok).toBe(true);
    expect(api.adoptStoryVariant).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({
        source_variant_id: "variant_alt",
        target_variant_id: "main",
        node_ids: ["n3"],
        mode: "exact",
      }),
    );
    expect(useStoryStore.getState().adoption?.conflict).toBe(false);

    api.adoptStoryVariant.mockRejectedValue(
      new WorkspaceApiError("STORY_ADOPTION_CONFLICT", "目标已有同 ID 对象", "确认覆盖", 409, "采用", {
        conflicts: [{ kind: "node", id: "n2", reason: "内容不同" }],
      }),
    );
    const failed = await useStoryStore.getState().adopt(["n3"]);

    expect(failed).toBe(false);
    const adoption = useStoryStore.getState().adoption;
    expect(adoption?.conflict).toBe(true);
    expect(adoption?.plan.conflicts[0]?.id).toBe("n2");
  });

  it("从备选切回主方案时清空选中节点", async () => {
    api.listStoryVariants.mockResolvedValue({
      revision: "doc_1",
      primary_variant_id: "main",
      variants: [VARIANT, ALT],
    });
    await useStoryStore.getState().load("w1");
    await useStoryStore.getState().selectNode("n1");

    await useStoryStore.getState().selectVariant("variant_alt");

    expect(useStoryStore.getState().variantId).toBe("variant_alt");
    expect(useStoryStore.getState().nodeId).toBeNull();
    expect(useStoryStore.getState().detail).toBeNull();
  });
});
