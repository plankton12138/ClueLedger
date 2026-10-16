import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchManuscript, fetchV2Chapter, saveV2Chapter } from "../api/v2";
import { loadRegistry, useV2Store } from "./v2";
vi.mock("../api/v2", () => ({
  fetchManuscript: vi.fn(),
  fetchV2Chapter: vi.fn(),
  saveV2Chapter: vi.fn(),
  fetchPreferences: vi.fn(async () => ({ preferences: [] })),
  fetchStorySnapshot: vi.fn(async () => ({ artifacts: [], commit_id: "head" })),
  listAgentJobs: vi.fn(async () => []),
  listChanges: vi.fn(async () => ({ changes: [] })),
}));
const chapter = {
  chapter_id: "one",
  title: "第一章",
  order_key: "1",
  chapter_base: "base",
  units: [],
  text: "原文",
  project_id: "a",
  commit_id: "head",
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
  useV2Store.getState().selectProject(null, true);
  useV2Store.getState().selectProject("a");
  vi.mocked(fetchV2Chapter).mockResolvedValue(chapter);
});
describe("V2 正文与项目隔离", () => {
  it("项目列表不再载入旧工作区迁移登记", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => JSON.stringify([
        { projectId: "native", name: "新作品", source: "created" },
        { projectId: "legacy", name: "旧测试文章", source: "migrated", slug: "test1" },
      ]),
    });
    expect(loadRegistry()).toEqual([
      { projectId: "native", name: "新作品", source: "created" },
    ]);
  });
  it("拒绝切走未保存正文，明确放弃后才切换", async () => {
    await useV2Store.getState().selectChapter("one");
    useV2Store.getState().setDraft("未保存");
    expect(useV2Store.getState().selectProject("b")).toBe(false);
    expect(useV2Store.getState().chapter.draft).toBe("未保存");
    expect(useV2Store.getState().selectProject("b", true)).toBe(true);
    expect(useV2Store.getState().chapter.draft).toBeNull();
  });
  it("切换项目后丢弃旧项目的晚到目录", async () => {
    let finish!: (value: Awaited<ReturnType<typeof fetchManuscript>>) => void;
    vi.mocked(fetchManuscript).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const loading = useV2Store.getState().refreshManuscript();
    useV2Store.getState().selectProject("b");
    finish({ project_id: "a", commit_id: "old", chapters: [chapter] });
    await loading;
    expect(useV2Store.getState().chapters).toEqual([]);
    expect(useV2Store.getState().head).toBeNull();
  });
  it("保存冲突释放忙状态并保留草稿与旧基础", async () => {
    await useV2Store.getState().selectChapter("one");
    useV2Store.getState().setDraft("我的编辑");
    vi.mocked(saveV2Chapter).mockRejectedValueOnce(
      new Error("版本冲突，请重新读取"),
    );
    await useV2Store.getState().saveChapter();
    const state = useV2Store.getState().chapter;
    expect(state.saving).toBe(false);
    expect(state.draft).toBe("我的编辑");
    expect(state.chapterBase).toBe("base");
    expect(state.error).toContain("版本冲突");
  });
  it("保存期间继续编辑，成功回执不能覆盖新输入", async () => {
    await useV2Store.getState().selectChapter("one");
    useV2Store.getState().setDraft("提交的稿子");
    let finish!: (value: Awaited<ReturnType<typeof saveV2Chapter>>) => void;
    vi.mocked(saveV2Chapter).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const saving = useV2Store.getState().saveChapter();
    useV2Store.getState().setDraft("继续写的新稿");
    finish({
      status: "committed",
      commit_id: "next",
      new_head: "next",
      summary: "保存",
      chapter_base: "base2",
    });
    await saving;
    expect(useV2Store.getState().chapter.baseline).toBe("提交的稿子");
    expect(useV2Store.getState().chapter.draft).toBe("继续写的新稿");
  });
});
