import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/ideas", () => ({
  createIdea: vi.fn(),
  getIdea: vi.fn(),
  listIdeaRevisions: vi.fn(),
  listIdeas: vi.fn(),
  requestKey: () => "idea-test-key",
  restoreIdea: vi.fn(),
  trashIdea: vi.fn(),
  updateIdea: vi.fn(),
}));

import {
  getIdea,
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
