/**
 * 工作区状态（0.4 V04-4）。
 *
 * 关注三件容易写错的事：换目录要清空旧选择、一轮结束后要回读磁盘与 Git、
 * 停止只发请求不假装已经结束。API 模块整体替身，状态逻辑本身用真实代码。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listWorkspaces: vi.fn(),
  openWorkspace: vi.fn(),
  removeWorkspace: vi.fn(),
  renameWorkspace: vi.fn(),
  pruneStaleWorkspaces: vi.fn(),
  listFiles: vi.fn(),
  fetchReadingPosition: vi.fn(),
  readFile: vi.fn(),
  searchText: vi.fn(),
  importManuscript: vi.fn(),
  gitStatus: vi.fn(),
  gitDiff: vi.fn(),
  gitLog: vi.fn(),
  gitCommitDiff: vi.fn(),
  gitInit: vi.fn(),
  gitRecord: vi.fn(),
  gitRevert: vi.fn(),
  listConversations: vi.fn(),
  fetchConversationUi: vi.fn(),
  patchConversationUi: vi.fn(),
  createConversationWithText: vi.fn(),
  conversationMessages: vi.fn(),
  startTurn: vi.fn(),
  renameConversation: vi.fn(),
  streamEvents: vi.fn(),
}));

vi.mock("../api/workspace", async () => {
  const actual = await vi.importActual<typeof import("../api/workspace")>("../api/workspace");
  return { ...actual, ...api };
});

import { useConversationsStore } from "./conversations";
import { useWorkspaceStore } from "./workspace";

const CATALOG = {
  chapters: [{ path: "正文/0001 雨夜.md", kind: "chapter", title: "雨夜", order: "0001", exists: true, size: 10 }],
  characters: [
    { path: "设定集/角色小传/林远.md", kind: "character", title: "林远", order: "", exists: true, size: 12 },
  ],
  settings: [
    { path: "设定集/线索.md", kind: "setting", title: "线索", order: "", exists: true, size: 0 },
    { path: "设定集/真相.md", kind: "setting", title: "真相", order: "", exists: true, size: 0 },
  ],
};

const STATUS = {
  initialized: true,
  branch: "master",
  head: "abc1234",
  staged: [],
  changes: [],
  conflicts: [],
  clean: true,
};

function reset(): void {
  useWorkspaceStore.setState(useWorkspaceStore.getInitialState(), true);
  useConversationsStore.getState().reset();
}

beforeEach(() => {
  reset();
  vi.clearAllMocks();
  api.listWorkspaces.mockResolvedValue([
    { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
  ]);
  api.listFiles.mockResolvedValue(CATALOG);
  api.fetchReadingPosition.mockResolvedValue({ path: null, digest: "", offset: 0 });
  api.listConversations.mockResolvedValue({
    items: [],
    recent_id: null,
    active_turn: null,
    revision: "r1",
  });
  api.fetchConversationUi.mockResolvedValue({ recent_id: null, drafts: {}, revision: "r1" });
  api.gitStatus.mockResolvedValue(STATUS);
  api.gitDiff.mockResolvedValue({ diff: "" });
  api.gitLog.mockResolvedValue({ commits: [] });
  api.readFile.mockImplementation(async (_id: string, path: string) => ({
    path,
    kind: path.startsWith("设定集") ? "setting" : "chapter",
    title: "雨夜",
    text: "# 雨夜",
    digest: "d1",
    size: 4,
  }));
});

describe("登记列表刷新", () => {
  it("派生后重新读取登记，但不改变当前作品", async () => {
    useWorkspaceStore.setState({ currentId: "w1", bindings: [] });
    api.listWorkspaces.mockResolvedValue([
      { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
      { id: "w2", name: "另一条线", path: "C:/另一条线", exists: true },
    ]);

    await useWorkspaceStore.getState().refreshBindings();

    const state = useWorkspaceStore.getState();
    expect(state.bindings.map((item) => item.id)).toEqual(["w1", "w2"]);
    expect(state.currentId).toBe("w1");
    expect(api.listFiles).not.toHaveBeenCalled();
  });
});

describe("目录与文件", () => {
  it("初始化选中第一个可用目录并加载目录、对话与 Git", async () => {
    await useWorkspaceStore.getState().init();

    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().catalog.chapters).toHaveLength(1);
    expect(useWorkspaceStore.getState().git?.initialized).toBe(true);
  });

  it("打开文件后记录选中路径，并带出原文", async () => {
    await useWorkspaceStore.getState().init();

    await useWorkspaceStore.getState().openPath("正文/0001 雨夜.md");

    expect(api.readFile).toHaveBeenCalledWith("w1", "正文/0001 雨夜.md");
    expect(useWorkspaceStore.getState().file?.path).toBe("正文/0001 雨夜.md");
  });

  it("换目录清空旧文件与对话，不把上一部作品带过去", async () => {
    await useWorkspaceStore.getState().init();
    await useWorkspaceStore.getState().openPath("正文/0001 雨夜.md");
    api.listFiles.mockResolvedValue({ chapters: [], characters: [], settings: [] });

    await useWorkspaceStore.getState().selectWorkspace("w1");

    expect(useWorkspaceStore.getState().file).toBeNull();
    expect(useWorkspaceStore.getState().selectedPath).toBeNull();
    // 会话由单独的 store 管理：换作品时它自己重新加载，不留在文件状态里。
    expect(useConversationsStore.getState().workspaceId).toBe("w1");
  });

  it("清理失效目录后切到仍在册的作品并提示数量", async () => {
    const alive = { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true };
    const stale = { id: "w2", name: "旧作", path: "C:/旧作", exists: false };
    await useWorkspaceStore.getState().init();
    useWorkspaceStore.setState({ bindings: [stale, alive], currentId: "w2" });
    api.pruneStaleWorkspaces.mockResolvedValue({ removed: [stale], workspaces: [alive] });

    await useWorkspaceStore.getState().pruneMissing();

    expect(api.pruneStaleWorkspaces).toHaveBeenCalled();
    expect(useWorkspaceStore.getState().bindings).toEqual([alive]);
    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().notice).toBe("已清理 1 个目录已不存在的作品。");
  });

  it("刷新仍存在的角色小传时保留选择并回读原文", async () => {
    await useWorkspaceStore.getState().init();
    const path = "设定集/角色小传/林远.md";
    await useWorkspaceStore.getState().openPath(path);
    api.readFile.mockClear();
    await useWorkspaceStore.getState().refreshAll();
    expect(api.readFile).toHaveBeenCalledWith("w1", path);
    expect(useWorkspaceStore.getState().selectedPath).toBe(path);
  });

  it("角色被外部改名后刷新清空旧选择，并显示新目录", async () => {
    await useWorkspaceStore.getState().init();
    await useWorkspaceStore.getState().openPath("设定集/角色小传/林远.md");
    api.listFiles.mockResolvedValue({
      ...CATALOG,
      characters: [{ ...CATALOG.characters[0], path: "设定集/角色小传/林侦探.md" }],
    });
    await useWorkspaceStore.getState().refreshAll();
    expect(useWorkspaceStore.getState().selectedPath).toBeNull();
    expect(useWorkspaceStore.getState().catalog.characters[0]?.path)
      .toBe("设定集/角色小传/林侦探.md");
  });
});

describe("删除作品", () => {
  it("删除当前作品后切到仍在册的作品并提示实际删除", async () => {
    await useWorkspaceStore.getState().init();
    const next = { id: "w2", name: "另一条线", path: "C:/另一条线", exists: true };
    api.removeWorkspace.mockResolvedValue({
      removed: { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
      workspaces: [next],
    });

    expect(await useWorkspaceStore.getState().remove("w1", "C:/作品")).toBe(true);
    expect(api.removeWorkspace).toHaveBeenCalledWith("w1", "C:/作品");
    expect(useWorkspaceStore.getState().currentId).toBe("w2");
    expect(useWorkspaceStore.getState().bindings).toEqual([next]);
    expect(useWorkspaceStore.getState().notice).toContain("及本地文件");
    expect(useWorkspaceStore.getState().positions.w1).toBeUndefined();
  });

  it("删除最后一个作品后清空当前选择与会话", async () => {
    await useWorkspaceStore.getState().init();
    api.removeWorkspace.mockResolvedValue({
      removed: { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
      workspaces: [],
    });

    expect(await useWorkspaceStore.getState().remove("w1", "C:/作品")).toBe(true);
    expect(useWorkspaceStore.getState().currentId).toBeNull();
    expect(useWorkspaceStore.getState().file).toBeNull();
    expect(useConversationsStore.getState().workspaceId).toBeNull();
  });

  it("删除失败时保留当前作品并显示错误", async () => {
    await useWorkspaceStore.getState().init();
    api.removeWorkspace.mockRejectedValueOnce(new Error("还在运行"));

    expect(await useWorkspaceStore.getState().remove("w1", "C:/作品")).toBe(false);
    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().error).toMatch(/^\[REQUEST_FAILED\]/);
  });
});

describe("重命名作品", () => {
  it("重命名当前作品后切到新标识", async () => {
    await useWorkspaceStore.getState().init();
    const renamed = { id: "w2", name: "新名字", path: "C:/新名字", exists: true };
    api.renameWorkspace.mockResolvedValue({ workspace: renamed, workspaces: [renamed] });

    expect(await useWorkspaceStore.getState().rename("w1", "新名字")).toBe("w2");
    expect(useWorkspaceStore.getState().currentId).toBe("w2");
    expect(useWorkspaceStore.getState().bindings).toEqual([renamed]);
    expect(useWorkspaceStore.getState().notice).toContain("新名字");
  });

  it("重命名其他作品时保持当前作品不变", async () => {
    await useWorkspaceStore.getState().init();
    const other = { id: "w2", name: "另一条线", path: "C:/另一条线", exists: true };
    useWorkspaceStore.setState({ bindings: [useWorkspaceStore.getState().bindings[0]!, other] });
    const renamed = { id: "w3", name: "改名后", path: "C:/改名后", exists: true };
    api.renameWorkspace.mockResolvedValue({
      workspace: renamed,
      workspaces: [useWorkspaceStore.getState().bindings[0]!, renamed],
    });

    expect(await useWorkspaceStore.getState().rename("w2", "改名后")).toBe("w3");
    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().bindings.map((item) => item.id)).toEqual(["w1", "w3"]);
  });

  it("重命名失败时保留当前作品并显示错误", async () => {
    await useWorkspaceStore.getState().init();
    api.renameWorkspace.mockRejectedValueOnce(new Error("同名目录已经存在"));

    expect(await useWorkspaceStore.getState().rename("w1", "新名字")).toBeNull();
    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().error).toMatch(/^\[REQUEST_FAILED\]/);
  });
});

describe("Git 动作", () => {
  it("新建作品成功后返回作品标识并切到新作品", async () => {
    await useWorkspaceStore.getState().init();
    const created = { id: "w2", name: "新作品", path: "C:/新作品", exists: true };
    api.openWorkspace.mockResolvedValue(created);
    api.listWorkspaces.mockResolvedValue([
      { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
      created,
    ]);

    expect(await useWorkspaceStore.getState().open("C:/新作品", true)).toBe("w2");
    expect(useWorkspaceStore.getState().currentId).toBe("w2");
  });

  it("打开目录失败返回失败并保留当前作品", async () => {
    await useWorkspaceStore.getState().init();
    api.openWorkspace.mockRejectedValueOnce(new Error("不可用"));
    expect(await useWorkspaceStore.getState().open("C:/不存在的作品", false)).toBeNull();
    expect(useWorkspaceStore.getState().currentId).toBe("w1");
    expect(useWorkspaceStore.getState().error).toMatch(/^\[REQUEST_FAILED\]/);
  });

  it("旧作品的回读失败不会写入切换后的错误区", async () => {
    await useWorkspaceStore.getState().init();
    let reject!: (reason: Error) => void;
    api.listFiles.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
    const pending = useWorkspaceStore.getState().refreshAll();
    await useWorkspaceStore.getState().selectWorkspace("w2");
    reject(new Error("旧失败")); await pending;
    expect(useWorkspaceStore.getState().currentId).toBe("w2");
    expect(useWorkspaceStore.getState().error).toBeNull();
  });

  it("补记后刷新状态与历史", async () => {
    await useWorkspaceStore.getState().init();
    api.gitRecord.mockResolvedValue({ commit: "abc1234" });

    await useWorkspaceStore.getState().recordChanges();

    expect(api.gitRecord).toHaveBeenCalledWith("w1");
    expect(useWorkspaceStore.getState().notice).toBe("已记录本次改动");
    expect(api.gitLog).toHaveBeenCalled();
  });

  it("撤销后清空选中的提交并回读文件", async () => {
    await useWorkspaceStore.getState().init();
    useWorkspaceStore.setState({
      selectedCommit: { commit: "abc1234", at: "", subject: "写作：写第一章" },
    });
    api.gitRevert.mockResolvedValue({ commit: "def5678" });

    await useWorkspaceStore.getState().revert("abc1234");

    expect(api.gitRevert).toHaveBeenCalledWith("w1", "abc1234");
    expect(useWorkspaceStore.getState().selectedCommit).toBeNull();
    expect(useWorkspaceStore.getState().notice).toBe("已撤销这次修改");
  });
});
