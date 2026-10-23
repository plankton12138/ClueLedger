/** 资料数据状态（0.7）：切作品后迟到的响应不能写进新作品的页面。 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/storyMaterials", () => ({
  fetchMaterials: vi.fn(),
}));

import { fetchMaterials } from "../api/storyMaterials";
import { materialsKey, useStoryMaterialsStore } from "./storyMaterials";

const mocked = vi.mocked(fetchMaterials);

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((resolveFn, rejectFn) => {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise, resolve, reject };
}

const sample = {
  domain: "timeline" as const,
  label: "时间线",
  as_of_chapter: 2,
  current_chapter: 2,
  chapters: [],
  entities: [],
  entries: [],
  total: 0,
  next_cursor: "",
  coverage: { total: 0, found: 0, empty: 0, insufficient: 0, unrecorded: 0, stale: 0 },
  warnings: [],
  conflicts: [],
};

describe("storyMaterials store", () => {
  beforeEach(() => {
    mocked.mockReset();
    useStoryMaterialsStore.getState().reset(null);
  });

  it("按截止章缓存，不同时点互不覆盖", async () => {
    mocked.mockResolvedValue(sample);
    const store = useStoryMaterialsStore.getState();
    await store.load("ws1", "timeline", { asOfChapter: 1 });
    await store.load("ws1", "timeline", { asOfChapter: 2 });
    expect(mocked).toHaveBeenCalledTimes(2);
    expect(useStoryMaterialsStore.getState().cache[materialsKey("timeline", 1)]).toBeTruthy();
    expect(useStoryMaterialsStore.getState().cache[materialsKey("timeline", 2)]).toBeTruthy();
  });

  it("已有缓存时不重复请求，除非要求刷新", async () => {
    mocked.mockResolvedValue(sample);
    const store = useStoryMaterialsStore.getState();
    await store.load("ws1", "timeline", { asOfChapter: 1 });
    await store.load("ws1", "timeline", { asOfChapter: 1 });
    expect(mocked).toHaveBeenCalledTimes(1);
    await store.load("ws1", "timeline", { asOfChapter: 1, refresh: true });
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it("切换作品后，上一部作品的迟到响应被丢弃", async () => {
    const slow = deferred<typeof sample>();
    mocked.mockReturnValueOnce(slow.promise);
    const store = useStoryMaterialsStore.getState();
    const pending = store.load("ws1", "timeline", { asOfChapter: 1 });
    useStoryMaterialsStore.getState().reset("ws2");
    slow.resolve(sample);
    await pending;
    const state = useStoryMaterialsStore.getState();
    expect(state.workspaceId).toBe("ws2");
    expect(state.cache[materialsKey("timeline", 1)]).toBeUndefined();
  });
});
