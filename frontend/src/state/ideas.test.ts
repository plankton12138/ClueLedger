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
  createIdea,
  getIdea,
  getIdeaRevision,
  listIdeaRevisions,
  listIdeas,
  restoreIdea,
  trashIdea,
  updateIdea,
  type IdeaSummary,
  type IdeaView,
} from "../api/ideas";
import { draftDirty, scopeOf, useIdeasStore } from "./ideas";

const saved: IdeaView = {
  idea_id: "idea-1",
  head_revision_id: "revision-1",
  revision_id: "revision-1",
  parent: null,
  created_at: "2026-10-24T10:00:00+00:00",
  updated_at: "2026-10-24T10:00:00+00:00",
  title: "",
  body: "已保存内容",
  tags: [],
  deleted: false,
  operation: "create",
  restored_from: null,
  origin: null,
  uses: [],
  digest: "digest-1",
};

function summary(ideaId: string, body = "摘要"): IdeaSummary {
  return {
    idea_id: ideaId,
    head_revision_id: `r-${ideaId}`,
    title: "",
    excerpt: body,
    excerpt_truncated: false,
    body_chars: body.length,
    tags: [],
    deleted: false,
    created_at: "2026-10-24T10:00:00+00:00",
    updated_at: "2026-10-24T10:00:00+00:00",
  };
}

function scope(workspaceId = "workspace-a") {
  return scopeOf(useIdeasStore.getState(), workspaceId);
}

beforeEach(() => {
  vi.resetAllMocks();
  useIdeasStore.setState(useIdeasStore.getInitialState(), true);
  vi.mocked(listIdeas).mockResolvedValue({ items: [], total: 0, invalid: 0, next_cursor: null });
  vi.mocked(listIdeaRevisions).mockResolvedValue({ items: [], total: 0, next_cursor: null });
  vi.mocked(getIdea).mockResolvedValue(saved);
});

describe("灵感草稿保护", () => {
  it("列表与历史消费服务端游标，追加时不覆盖原有条目", async () => {
    vi.mocked(listIdeas).mockImplementation(async (_ws, options) => ({
      items: [summary(options?.cursor ? "next" : "first")],
      total: 2,
      invalid: 0,
      next_cursor: options?.cursor ? null : "c2",
    }));
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().loadMore();
    expect(useIdeasStore.getState().items.map(item => item.idea_id)).toEqual(["first", "next"]);

    vi.mocked(listIdeaRevisions).mockImplementation(async (_ws, _id, options) => ({
      items: [
        {
          id: options?.cursor ? "older" : "r",
          parent: null,
          created_at: "",
          operation: "edit",
          restored_from: null,
          deleted: false,
          title: "",
          chars: 3,
        },
      ],
      total: 2,
      next_cursor: options?.cursor ? null : "h2",
    }));
    await useIdeasStore.getState().select(saved.idea_id);
    await useIdeasStore.getState().loadMoreRevisions();
    expect(scope().revisions.map(item => item.id)).toEqual(["r", "older"]);
  });

  it("历史预览乱序与切条目不会显示旧请求的内容", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    let resolve!: (view: Awaited<ReturnType<typeof getIdeaRevision>>) => void;
    vi.mocked(getIdeaRevision)
      .mockReturnValueOnce(new Promise(done => { resolve = done; }))
      .mockResolvedValue({ ...saved, revision_id: "new-preview" });
    const pending = useIdeasStore.getState().previewRevision("slow");
    await useIdeasStore.getState().previewRevision("new-preview");
    resolve({ ...saved, revision_id: "slow" });
    await pending;
    expect(useIdeasStore.getState().preview?.revision_id).toBe("new-preview");
    useIdeasStore.getState().clearSelection();
    expect(useIdeasStore.getState().preview).toBeNull();
  });

  it("删除修订超过一页时继续查询，恢复最近未删除内容", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.setState(state => ({
      scopes: {
        ...state.scopes,
        "workspace-a": {
          ...state.scopes["workspace-a"],
          selected: { ...saved, deleted: true },
          editing: {},
        },
      },
    }));
    vi.mocked(listIdeaRevisions)
      .mockResolvedValueOnce({ items: [], total: 21, next_cursor: "older" })
      .mockResolvedValueOnce({
        items: [
          {
            id: "alive",
            parent: null,
            created_at: "",
            operation: "create",
            restored_from: null,
            deleted: false,
            title: "",
            chars: 3,
          },
        ],
        total: 21,
        next_cursor: null,
      });
    vi.mocked(restoreIdea).mockResolvedValue(saved);
    await useIdeasStore.getState().restoreEntry(saved.idea_id);
    expect(restoreIdea).toHaveBeenCalledWith(
      "workspace-a",
      saved.idea_id,
      expect.objectContaining({ target_revision: "alive" }),
    );
  });

  it("同作品切换条目并搜索后返回，分别保留修改稿", async () => {
    vi.mocked(getIdea).mockImplementation(async (_ws, id) => ({ ...saved, idea_id: id, body: `${id}原文` }));
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select("A");
    useIdeasStore.getState().setEntryBody("A", "A草稿");
    await useIdeasStore.getState().select("B");
    useIdeasStore.getState().setEntryBody("B", "B草稿");
    await useIdeasStore.getState().setQuery("过滤");
    await useIdeasStore.getState().select("A");

    expect(scope().query).toBe("过滤");
    expect(scope().editing.A.body).toBe("A草稿");
    expect(draftDirty(scope().editing.A)).toBe(true);
    expect(scope().selected?.body).toBe("A原文");

    useIdeasStore.getState().discardEntry("A");
    await useIdeasStore.getState().select("B");
    expect(scope().editing.B.body).toBe("B草稿");
  });

  it("快速记录只有一个草稿，提交成功后清空并保持可继续记录", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().setCaptureBody("第一份");
    vi.mocked(createIdea).mockResolvedValue({ ...saved, idea_id: "idea-new", body: "第一份" });
    const result = await useIdeasStore.getState().capture();

    expect(result.status).toBe("saved");
    expect(scope().capture.body).toBe("");
    expect(scope().activeId).toBe("idea-new");
    expect(createIdea).toHaveBeenCalledTimes(1);
  });

  it("保存期间的新输入留在同一条灵感的修改稿里，不另建第二条记录", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().setCaptureBody("提交内容");
    let resolve!: (view: IdeaView) => void;
    vi.mocked(createIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    const pending = useIdeasStore.getState().capture();
    useIdeasStore.getState().setCaptureBody("提交内容 后来补充");
    resolve({ ...saved, idea_id: "idea-new", body: "提交内容" });
    await pending;

    expect(scope().capture.body).toBe("");
    expect(scope().editing["idea-new"]?.body).toBe("提交内容 后来补充");
    expect(draftDirty(scope().editing["idea-new"])).toBe(true);
    expect(createIdea).toHaveBeenCalledTimes(1);
  });

  it("保存响应到达前的新输入不被清空", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "提交内容");
    let resolve!: (view: IdeaView) => void;
    vi.mocked(updateIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    const pending = useIdeasStore.getState().saveEntry(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "后来输入");
    resolve({ ...saved, body: "提交内容", revision_id: "r2", digest: "d2" });
    await pending;

    expect(scope().editing[saved.idea_id].body).toBe("后来输入");
    expect(draftDirty(scope().editing[saved.idea_id])).toBe(true);
    expect(scope().selected?.revision_id).toBe("r2");
  });

  it("保存中切换作品，返回后使用原结果，不重复创建灵感", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().setCaptureBody("新灵感");
    let resolve!: (view: IdeaView) => void;
    vi.mocked(createIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    const pending = useIdeasStore.getState().capture();
    await useIdeasStore.getState().load("workspace-b");
    resolve({ ...saved, body: "新灵感" });
    await pending;

    expect(scope("workspace-b").capture.body).toBe("");
    expect(scope("workspace-a").capture.body).toBe("");
    // 内容没有在保存期间变化：不留下多余修改稿，选中时再按服务端版本建立基线。
    expect(scope("workspace-a").editing[saved.idea_id]).toBeUndefined();
    expect(createIdea).toHaveBeenCalledTimes(1);
  });

  it("结果未知时重试沿用请求键与提交体", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "网络重试");
    vi.mocked(updateIdea)
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue({ ...saved, body: "网络重试", revision_id: "r2" });
    const first = await useIdeasStore.getState().saveEntry(saved.idea_id);
    const second = await useIdeasStore.getState().saveEntry(saved.idea_id);

    expect(first.status).toBe("failed");
    expect(second.status).toBe("saved");
    expect(vi.mocked(updateIdea).mock.calls[0]).toEqual(vi.mocked(updateIdea).mock.calls[1]);
  });

  it("内容没有变化时保存返回 unchanged 且不产生新修订", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "已保存内容");
    vi.mocked(updateIdea).mockResolvedValue(saved);
    const result = await useIdeasStore.getState().saveEntry(saved.idea_id);

    expect(result.status).toBe("unchanged");
    expect(scope().editing[saved.idea_id].base?.revisionId).toBe(saved.revision_id);
  });

  it("已有条目的未保存修改在切页后保留", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "改过但没有保存");

    await useIdeasStore.getState().load("workspace-a");

    expect(scope().selected).toEqual(saved);
    expect(scope().editing[saved.idea_id].body).toBe("改过但没有保存");
    expect(updateIdea).not.toHaveBeenCalled();
  });

  it("作品分别保留草稿，切换后不会混入其他作品内容", async () => {
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().setCaptureBody("作品 A 草稿");
    await useIdeasStore.getState().load("workspace-b");
    expect(scope("workspace-b").capture.body).toBe("");
    useIdeasStore.getState().setCaptureBody("作品 B 草稿");

    await useIdeasStore.getState().load("workspace-a");
    expect(scope("workspace-a").capture.body).toBe("作品 A 草稿");
    await useIdeasStore.getState().load("workspace-b");
    expect(scope("workspace-b").capture.body).toBe("作品 B 草稿");
  });

  it("作品切走再切回时，旧详情请求不能覆盖当前内容", async () => {
    let resolve!: (view: IdeaView) => void;
    vi.mocked(getIdea).mockReturnValue(new Promise(done => { resolve = done; }));
    await useIdeasStore.getState().load("workspace-a");
    const pending = useIdeasStore.getState().select(saved.idea_id);
    await useIdeasStore.getState().load("workspace-b");
    await useIdeasStore.getState().load("workspace-a");
    useIdeasStore.getState().setCaptureBody("返回后输入的新草稿");

    resolve(saved);
    await pending;

    expect(scope("workspace-a").capture.body).toBe("返回后输入的新草稿");
    expect(scope("workspace-a").selected).toBeNull();
  });

  it("有未保存修改时删除与恢复历史都不执行", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "保留这段输入");

    await useIdeasStore.getState().trashEntry(saved.idea_id);
    await useIdeasStore.getState().restoreRevision("revision-old");

    expect(trashIdea).not.toHaveBeenCalled();
    expect(restoreIdea).not.toHaveBeenCalled();
    expect(scope().editing[saved.idea_id].body).toBe("保留这段输入");

    useIdeasStore.getState().discardEntry(saved.idea_id);
    expect(scope().editing[saved.idea_id].body).toBe(saved.body);
    expect(draftDirty(scope().editing[saved.idea_id])).toBe(false);
  });

  it("保存冲突后切页返回仍保留输入", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "冲突后的草稿");
    vi.mocked(updateIdea).mockRejectedValue(new Error("版本冲突"));

    await useIdeasStore.getState().saveEntry(saved.idea_id);
    await useIdeasStore.getState().load("workspace-a");

    expect(scope().editing[saved.idea_id].body).toBe("冲突后的草稿");
    expect(scope().selected?.digest).toBe(saved.digest);
  });
});

describe("引用前的固定版本", () => {
  it("有脏稿先保存，返回保存结果的新修订", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "改过的正文");
    vi.mocked(updateIdea).mockResolvedValue({
      ...saved,
      revision_id: "r2",
      digest: "d2",
      body: "改过的正文",
    });

    const result = await useIdeasStore.getState().prepareReference(saved.idea_id);

    expect(updateIdea).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ ideaId: saved.idea_id, revisionId: "r2", body: "改过的正文" });
  });

  it("没有修改时直接使用当前基线，不额外保存", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);

    const result = await useIdeasStore.getState().prepareReference(saved.idea_id);

    expect(updateIdea).not.toHaveBeenCalled();
    expect(result).toMatchObject({ revisionId: saved.revision_id, body: saved.body });
  });

  it("保存失败时不返回可引用的版本", async () => {
    await useIdeasStore.getState().load("workspace-a");
    await useIdeasStore.getState().select(saved.idea_id);
    useIdeasStore.getState().setEntryBody(saved.idea_id, "冲突的正文");
    vi.mocked(updateIdea).mockRejectedValue(new Error("版本冲突"));

    expect(await useIdeasStore.getState().prepareReference(saved.idea_id)).toBeNull();
  });
});
