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
  pruneStaleWorkspaces: vi.fn(),
  listFiles: vi.fn(),
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
  stopTurn: vi.fn(),
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

describe("Git 动作", () => {
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
