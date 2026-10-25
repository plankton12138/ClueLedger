import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/ideas", () => ({
  listIdeaActivity: vi.fn(),
}));

import { listIdeaActivity, type IdeaActivity, type IdeaActivityPage } from "../api/ideas";
import { useIdeaActivityStore } from "./ideaActivity";

function turn(id: string, kind: "referenced" | "expanded" = "referenced"): IdeaActivity {
  return {
    id,
    kind,
    idea_id: "idea-1",
    revision_id: "rev-1",
    conversation_id: `conv-${id}`,
    conversation_title: `讨论 ${id}`,
    conversation_available: true,
    turn_id: `turn-${id}`,
    at: "2026-10-25T10:00:00+08:00",
  };
}

function page(overrides: Partial<IdeaActivityPage> = {}): IdeaActivityPage {
  return { items: [], total: 0, complete: true, next_cursor: null, ...overrides };
}

beforeEach(() => {
  vi.resetAllMocks();
  useIdeaActivityStore.setState(useIdeaActivityStore.getInitialState(), true);
});

describe("使用方法记录（I5）", () => {
  it("读到一页就记下完整度与游标", async () => {
    vi.mocked(listIdeaActivity).mockResolvedValue(
      page({ items: [turn("a")], total: 2, complete: false }),
    );

    await useIdeaActivityStore.getState().load("w1", "idea-1");

    const state = useIdeaActivityStore.getState();
    expect(state.items.map(item => item.id)).toEqual(["a"]);
    // 标题显示总条数，不把「已读到的这一页」当成全部。
    expect(state.total).toBe(2);
    expect(state.complete).toBe(false);
    expect(state.cursor).toBeNull();
    expect(listIdeaActivity).toHaveBeenCalledWith("w1", "idea-1");
  });

  it("整理完的下一页按游标追加，不重复同一条", async () => {
    vi.mocked(listIdeaActivity).mockResolvedValueOnce(
      page({ items: [turn("a")], total: 2, next_cursor: "c2" }),
    );
    await useIdeaActivityStore.getState().load("w1", "idea-1");

    vi.mocked(listIdeaActivity).mockResolvedValueOnce(
      page({ items: [turn("a"), turn("b", "expanded")], total: 2 }),
    );
    await useIdeaActivityStore.getState().more();

    const state = useIdeaActivityStore.getState();
    expect(state.items.map(item => item.id)).toEqual(["a", "b"]);
    expect(state.cursor).toBeNull();
    expect(listIdeaActivity).toHaveBeenLastCalledWith("w1", "idea-1", { cursor: "c2" });
  });

  it("还在整理时不带游标重读，扫完换成完整快照", async () => {
    vi.mocked(listIdeaActivity).mockResolvedValueOnce(
      page({ items: [turn("a")], total: 2, complete: false }),
    );
    await useIdeaActivityStore.getState().load("w1", "idea-1");

    vi.mocked(listIdeaActivity).mockResolvedValueOnce(
      page({ items: [turn("b")], total: 2, complete: false }),
    );
    await useIdeaActivityStore.getState().more();
    expect(useIdeaActivityStore.getState().items.map(item => item.id)).toEqual(["a", "b"]);
    expect(listIdeaActivity).toHaveBeenLastCalledWith("w1", "idea-1", { cursor: null });

    // 扫完那一次：游标可能指向后面的页，条目以服务端这一页为准。
    vi.mocked(listIdeaActivity).mockResolvedValueOnce(
      page({ items: [turn("b"), turn("c")], total: 3, next_cursor: "c3" }),
    );
    await useIdeaActivityStore.getState().more();
    const state = useIdeaActivityStore.getState();
    expect(state.items.map(item => item.id)).toEqual(["b", "c"]);
    expect(state.complete).toBe(true);
    expect(state.cursor).toBe("c3");
  });

  it("切走之后迟到的结果不覆盖当前条目", async () => {
    const gate: { resolve?: (value: IdeaActivityPage) => void } = {};
    vi.mocked(listIdeaActivity).mockImplementationOnce(
      () => new Promise<IdeaActivityPage>(resolve => (gate.resolve = resolve)),
    );
    const slow = useIdeaActivityStore.getState().load("w1", "idea-1");

    vi.mocked(listIdeaActivity).mockResolvedValueOnce(page({ items: [turn("b")], total: 1 }));
    await useIdeaActivityStore.getState().load("w1", "idea-2");
    gate.resolve?.(page({ items: [turn("a")], total: 1 }));
    await slow;

    const state = useIdeaActivityStore.getState();
    expect(state.ideaId).toBe("idea-2");
    expect(state.items.map(item => item.id)).toEqual(["b"]);
  });

  it("读取失败时保留已有条目并给出可重试的说明", async () => {
    vi.mocked(listIdeaActivity).mockRejectedValueOnce(new Error("网络中断"));
    await useIdeaActivityStore.getState().load("w1", "idea-1");

    const state = useIdeaActivityStore.getState();
    expect(state.loading).toBe(false);
    expect(state.error).toContain("读取使用记录");
    expect(state.items).toEqual([]);
  });
});
