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
  createConversation: vi.fn(),
  conversationMessages: vi.fn(),
  startTurn: vi.fn(),
  stopTurn: vi.fn(),
  streamEvents: vi.fn(),
}));

vi.mock("../api/workspace", async () => {
  const actual = await vi.importActual<typeof import("../api/workspace")>("../api/workspace");
  return { ...actual, ...api };
});

import { useWorkspaceStore } from "./workspace";

const CATALOG = {
  chapters: [{ path: "正文/0001 雨夜.md", kind: "chapter", title: "雨夜", order: "0001", exists: true, size: 10 }],
  settings: [
    { path: "设定集/角色小传.md", kind: "setting", title: "角色小传", order: "", exists: true, size: 0 },
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
}

beforeEach(() => {
  reset();
  vi.clearAllMocks();
  api.listWorkspaces.mockResolvedValue([
    { id: "w1", name: "雪山旅馆", path: "C:/作品", exists: true },
  ]);
  api.listFiles.mockResolvedValue(CATALOG);
  api.listConversations.mockResolvedValue([]);
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
    api.listFiles.mockResolvedValue({ chapters: [], settings: [] });

    await useWorkspaceStore.getState().selectWorkspace("w1");

    expect(useWorkspaceStore.getState().file).toBeNull();
    expect(useWorkspaceStore.getState().selectedPath).toBeNull();
    expect(useWorkspaceStore.getState().events).toEqual([]);
  });
});

describe("一轮对话", () => {
  it("发送后按事件流推进，结束时记录结果并回读目录", async () => {
    await useWorkspaceStore.getState().init();
    api.createConversation.mockResolvedValue({
      id: "c1",
      title: "新的对话",
      created_at: "2026-10-20T10:00:00+08:00",
      updated_at: "2026-10-20T10:00:00+08:00",
    });
    api.startTurn.mockResolvedValue({
      turn_id: "t1",
      conversation_id: "c1",
      started_at: "2026-10-20T10:00:00+08:00",
    });
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      yield { seq: 2, at: "", type: "assistant_message", text: "写好了。" };
      yield {
        seq: 3,
        at: "",
        type: "run_finished",
        status: "completed",
        recording: "committed",
        changed_paths: ["正文/0001 雨夜.md"],
        commit: "abc1234",
      };
    });

    await useWorkspaceStore.getState().send("写第一章");

    const state = useWorkspaceStore.getState();
    expect(api.startTurn).toHaveBeenCalledWith("w1", "c1", "写第一章", expect.any(String));
    expect(state.events.map((event) => event.seq)).toEqual([1, 2, 3]);
    expect(state.running).toBe(false);
    expect(state.lastResult).toMatchObject({
      status: "completed",
      recording: "committed",
      changed_paths: ["正文/0001 雨夜.md"],
      commit: "abc1234",
    });
    // 结束必须回读目录与 Git：文件是磁盘上的事实。
    expect(api.listFiles).toHaveBeenCalledTimes(2);
    expect(api.gitStatus).toHaveBeenCalledTimes(2);
  });

  it("停止只发停止请求，不把状态假装成完成", async () => {
    await useWorkspaceStore.getState().init();
    useWorkspaceStore.setState({ activeTurn: "t1", running: true });
    api.stopTurn.mockResolvedValue({ stopped: true });

    await useWorkspaceStore.getState().stop();

    expect(api.stopTurn).toHaveBeenCalledWith("w1", "t1");
    expect(useWorkspaceStore.getState().running).toBe(true);
  });

  it("事件流断开时记录错误，不会卡在运行中", async () => {
    await useWorkspaceStore.getState().init();
    api.createConversation.mockResolvedValue({
      id: "c1",
      title: "新的对话",
      created_at: "",
      updated_at: "",
    });
    api.startTurn.mockResolvedValue({ turn_id: "t1", conversation_id: "c1", started_at: "" });
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      throw new Error("连接中断");
    });

    await useWorkspaceStore.getState().send("写第一章");

    expect(useWorkspaceStore.getState().running).toBe(false);
    expect(useWorkspaceStore.getState().error).toContain("连接中断");
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
