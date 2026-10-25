import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/ideas", () => ({
  createIdea: vi.fn(),
  getIdea: vi.fn(),
  getIdeaRevision: vi.fn(),
  listIdeaRevisions: vi.fn(),
  listIdeas: vi.fn(),
  requestKey: () => "idea-test-key",
  restoreIdea: vi.fn(),
  trashIdea: vi.fn(),
  updateIdea: vi.fn(),
}));

import {
  getIdea,
  getIdeaRevision,
  createIdea,
  listIdeaRevisions,
  listIdeas,
  restoreIdea,
  trashIdea,
  updateIdea,
  type IdeaView,
} from "../api/ideas";
import { useIdeasStore } from "./ideas";

const saved: IdeaView = {
  idea_id: "idea-1",
  head_revision_id: "revision-1",
  revision_id: "revision-1",
  parent: null,
  created_at: "2026-10-24",
  updated_at: "2026-10-24",
  title: "旧识",
  body: "已保存内容",
  tags: [],
  deleted: false,
  operation: "create",
  restored_from: null,
  origin: null,
  uses: [],
  digest: "digest-1",
};

beforeEach(() => {
  vi.resetAllMocks();
  useIdeasStore.setState(useIdeasStore.getInitialState(), true);
  vi.mocked(listIdeas).mockResolvedValue({ items: [], total: 0, invalid: 0, next_cursor: null });
  vi.mocked(listIdeaRevisions).mockResolvedValue({ items: [], total: 0, next_cursor: null });
  vi.mocked(getIdea).mockResolvedValue(saved);
});

describe("灵感草稿保护", () => {
  it("列表与历史消费服务端游标，追加时不覆盖原有条目", async () => {
    vi.mocked(listIdeas).mockImplementation(async (_ws, options) => ({ items: [{ idea_id: options?.cursor ? "next" : "first", head_revision_id: "r", title: "条目", excerpt: "摘要", tags: [], deleted: false, updated_at: "" }], total: 2, invalid: 0, next_cursor: options?.cursor ? null : "c2" }));
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().loadMore();
    expect(useIdeasStore.getState().items.map(item => item.idea_id)).toEqual(["first", "next"]);
    vi.mocked(listIdeaRevisions).mockImplementation(async (_ws, _id, options) => ({ items: [{ id: options?.cursor ? "older" : "r", parent: null, created_at: "", operation: "edit", restored_from: null, deleted: false, title: "条目", chars: 3 }], total: 2, next_cursor: options?.cursor ? null : "h2" }));
    await useIdeasStore.getState().select(saved.idea_id);
    await useIdeasStore.getState().loadMoreRevisions();
    expect(useIdeasStore.getState().revisions.map(item => item.id)).toEqual(["r", "older"]);
  });
  it("历史预览乱序与切条目不会显示旧请求的内容", async () => {
    await useIdeasStore.getState().load("workspace-a"); await useIdeasStore.getState().select(saved.idea_id);
    let resolve!: (view: Awaited<ReturnType<typeof getIdeaRevision>>) => void;
    vi.mocked(getIdeaRevision).mockReturnValueOnce(new Promise(done => { resolve = done; })).mockResolvedValue({ ...saved, revision_id: "new-preview" });
    const pending = useIdeasStore.getState().previewRevision("slow");
    await useIdeasStore.getState().previewRevision("new-preview");
    resolve({ ...saved, revision_id: "slow" }); await pending;
    expect(useIdeasStore.getState().preview?.revision_id).toBe("new-preview");
    useIdeasStore.getState().startNew(); expect(useIdeasStore.getState().preview).toBeNull();
  });
  it("删除修订超过一页时继续查询，恢复最近未删除内容", async () => {
    await useIdeasStore.getState().load("workspace-a"); await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.setState({ selected: { ...saved, deleted: true } });
    vi.mocked(listIdeaRevisions).mockResolvedValueOnce({ items: [], total: 21, next_cursor: "older" }).mockResolvedValueOnce({ items: [{ id: "alive", parent: null, created_at: "", operation: "create", restored_from: null, deleted: false, title: "旧内容", chars: 3 }], total: 21, next_cursor: null });
    vi.mocked(restoreIdea).mockResolvedValue(saved);
    await useIdeasStore.getState().restoreSelected();
    expect(restoreIdea).toHaveBeenCalledWith("workspace-a", saved.idea_id, expect.objectContaining({ target_revision: "alive" }));
  });
  it("同作品切换条目并搜索后返回，分别保留正文与基础修订", async () => {
    vi.mocked(getIdea).mockImplementation(async (_ws, id) => ({ ...saved, idea_id: id, body: `${id}原文` }));
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select("A");
    useIdeasStore.getState().setDraft({ body: "A草稿" });
    await useIdeasStore.getState().select("B");
    useIdeasStore.getState().setDraft({ body: "B草稿" });
    await useIdeasStore.getState().setQuery("过滤");
    await useIdeasStore.getState().select("A");
    expect(useIdeasStore.getState()).toMatchObject({ query: "过滤", draft: { body: "A草稿" }, selected: { body: "A原文" }, dirty: true });
    useIdeasStore.getState().cancel();
    await useIdeasStore.getState().select("B");
    expect(useIdeasStore.getState().draft.body).toBe("B草稿");
  });
  it("新建草稿各自有临时身份，可返回继续编辑", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().startNew();
    const first = useIdeasStore.getState().activeKey!;
    useIdeasStore.getState().setDraft({ body: "第一份草稿" });
    useIdeasStore.getState().startNew();
    useIdeasStore.getState().setDraft({ body: "第二份草稿" });
    expect(useIdeasStore.getState().activeKey).not.toBe(first);
    useIdeasStore.getState().selectDraft(first);
    expect(useIdeasStore.getState().draft.body).toBe("第一份草稿");
    useIdeasStore.getState().cancel();
    expect(useIdeasStore.getState().entryEditors["workspace-a"][first]).toBeUndefined();
  });
  it("保存响应到达前的新输入不被清空", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setDraft({ body: "提交内容" });
    let resolve!: (view: IdeaView) => void;
    vi.mocked(updateIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    const pending = useIdeasStore.getState().save();
    useIdeasStore.getState().setDraft({ body: "后来输入" });
    resolve({ ...saved, body: "提交内容", revision_id: "r2", digest: "d2" }); await pending;
    expect(useIdeasStore.getState()).toMatchObject({ draft: { body: "后来输入" }, dirty: true, selected: { revision_id: "r2" } });
  });
  it("保存中切换作品，返回后使用原结果，不重复创建灵感", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().startNew(); useIdeasStore.getState().setDraft({ body: "新灵感" });
    let resolve!: (view: IdeaView) => void;
    vi.mocked(createIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    const pending = useIdeasStore.getState().save();
    await useIdeasStore.getState().load("workspace-b");
    resolve({ ...saved, body: "新灵感" }); await pending;
    expect(useIdeasStore.getState().selected).toBeNull();
    await useIdeasStore.getState().load("workspace-a");
    expect(useIdeasStore.getState()).toMatchObject({ selectedId: saved.idea_id, dirty: false, newDraft: false });
    expect(createIdea).toHaveBeenCalledTimes(1);
  });
  it("结果未知时重试沿用请求键与提交体", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setDraft({ body: "网络重试" });
    vi.mocked(updateIdea).mockRejectedValueOnce(new TypeError("network")).mockResolvedValue({ ...saved, body: "网络重试" });
    await useIdeasStore.getState().save(); await useIdeasStore.getState().save();
    expect(vi.mocked(updateIdea).mock.calls[0]).toEqual(vi.mocked(updateIdea).mock.calls[1]);
  });
  it("新建草稿在切页后重新挂载时仍可编辑", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().startNew();
    useIdeasStore.getState().setDraft({ title: "未保存标题", body: "未保存正文" });

    await useIdeasStore.getState().load("workspace-a");

    expect(useIdeasStore.getState()).toMatchObject({
      newDraft: true,
      dirty: true,
      draft: { title: "未保存标题", body: "未保存正文" },
    });
  });

  it("已有条目的未保存修改和读取版本在切页后保留", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setDraft({ body: "改过但没有保存" });

    await useIdeasStore.getState().load("workspace-a");

    expect(useIdeasStore.getState()).toMatchObject({
      selected: saved,
      dirty: true,
      draft: { body: "改过但没有保存" },
    });
    expect(updateIdea).not.toHaveBeenCalled();
  });

  it("作品分别保留草稿，切换后不会混入其他作品内容", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().startNew();
    useIdeasStore.getState().setDraft({ body: "作品 A 草稿" });
    await useIdeasStore.getState().load("workspace-b");
    expect(useIdeasStore.getState().draft.body).toBe("");
    useIdeasStore.getState().startNew();
    useIdeasStore.getState().setDraft({ body: "作品 B 草稿" });

    await useIdeasStore.getState().load("workspace-a");
    expect(useIdeasStore.getState().draft.body).toBe("作品 A 草稿");
    expect(useIdeasStore.getState().dirty).toBe(true);
    await useIdeasStore.getState().load("workspace-b");
    expect(useIdeasStore.getState().draft.body).toBe("作品 B 草稿");
  });

  it("作品切走再切回时，旧详情请求不能覆盖新草稿", async () => {
    let resolve!: (view: IdeaView) => void;
    vi.mocked(getIdea).mockReturnValue(new Promise((done) => { resolve = done; }));
    await useIdeasStore.getState().load("workspace-a");
    const pending = useIdeasStore.getState().select(saved.idea_id);
    await useIdeasStore.getState().load("workspace-b");
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().startNew();
    useIdeasStore.getState().setDraft({ body: "返回后输入的新草稿" });

    resolve(saved);
    await pending;

    expect(useIdeasStore.getState().draft.body).toBe("返回后输入的新草稿");
    expect(useIdeasStore.getState().dirty).toBe(true);
    expect(useIdeasStore.getState().selected).toBeNull();
  });

  it("未保存时不能通过删除或恢复历史覆盖草稿", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setDraft({ body: "保留这段输入" });

    await useIdeasStore.getState().trashSelected();
    await useIdeasStore.getState().restoreRevision("revision-old");

    expect(trashIdea).not.toHaveBeenCalled();
    expect(restoreIdea).not.toHaveBeenCalled();
    expect(useIdeasStore.getState().draft.body).toBe("保留这段输入");
    useIdeasStore.getState().cancel();
    expect(useIdeasStore.getState().draft.body).toBe(saved.body);
    expect(useIdeasStore.getState().dirty).toBe(false);
  });

  it("保存冲突后切页返回仍保留输入", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setDraft({ body: "冲突后的草稿" });
    vi.mocked(updateIdea).mockRejectedValue(new Error("版本冲突"));

    await useIdeasStore.getState().save();
    await useIdeasStore.getState().load("workspace-a");

    expect(useIdeasStore.getState().draft.body).toBe("冲突后的草稿");
    expect(useIdeasStore.getState().dirty).toBe(true);
    expect(useIdeasStore.getState().selected?.digest).toBe(saved.digest);
  });
});
