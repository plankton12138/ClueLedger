/**
 * 写作台壳层行为测试（V03-10 M8）：收拢后桌面只剩三件——只读稿纸、右侧固定对话栏、
 * 右缘「设定集」标记；无任何编辑入口，界面无开发痕迹。
 *
 * 用 react-dom/server 静态渲染（项目偏好：不引入 jsdom）。zustand v5 在 SSR
 * 下以初始状态作 server snapshot，setState 不生效，故此处直接替换壳层依赖的
 * 两个 store 为可控替身；组件 effect 不执行，状态即渲染输入。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

const m = vi.hoisted(() => {
  const v2 = {
    projects: [{ projectId: "prj_seq9", name: "测试作品", source: "created" }],
    currentId: "prj_seq9" as string | null,
    head: "head-1",
    chapters: [{ chapter_id: "ch_1", title: "第一章 雨夜", chapter_base: "base-1" }],
    chapter: {
      chapterId: "ch_1",
      baseline: "雨下了一整夜。",
      draft: "雨下了一整夜。",
      chapterBase: "base-1",
      title: "第一章 雨夜",
      saving: false,
      loading: false,
      error: null as string | null,
      lastSavedAt: null,
      stale: false,
    },
    storyArtifacts: [],
    storyCommit: null,
    changes: [],
    jobs: [],
    error: null as string | null,
    selectedChangeId: null,
    focusArtifactId: null,
  };
  // V03-10 M7：操作结果缓存已随面板撤下移除，壳层只依赖主区与阅读位置。
  const desk = {
    projectId: "prj_seq9" as string | null,
    mainView: { kind: "manuscript", chapterId: "ch_1" } as unknown,
    readingPositions: {} as Record<string, unknown>,
  };
  const wrap = <T extends object>(state: T) =>
    Object.assign(
      (selector?: ((s: T) => unknown) | undefined) =>
        selector ? selector(state) : state,
      {
        getState: () => state,
        setState: () => {},
        subscribe: () => () => {},
      },
    );
  return { v2, desk, useV2: wrap(v2), useDesk: wrap(desk) };
});

// 对话 store：注入一条候选消息，验证候选「采用」在写作台右侧对话栏内接线。
const c = vi.hoisted(() => {
  const candidate = {
    message_id: "cand-1",
    conversation_id: "conv_1",
    turn_id: null,
    role: "assistant",
    kind: "candidate",
    text: "",
    refs: [],
    model: null,
    status: "completed",
    error: null,
    created_at: "",
    payload: {
      job_ref: "job_1",
      summary: "第一章草稿已就绪，共 6 章",
      target_ids: ["ch_1"],
      change_ref: null,
      accepted: false,
    },
  };
  const state = {
    views: {
      conv_1: {
        messages: [candidate],
        loading: false,
        error: null,
        pending: null,
        activeTurnId: null,
        abandoned: {},
      },
    } as Record<string, unknown>,
    service: null,
    serviceKind: "api",
    activeConversationId: "conv_1",
    projectId: "prj_seq9",
    conversationsByProject: {
      prj_seq9: [
        {
          conversation_id: "conv_1",
          project_id: "prj_seq9",
          title: "第一轮讨论",
          created_at: "",
          updated_at: "",
        },
      ],
    } as Record<string, unknown>,
    drafts: { conv_1: "" } as Record<string, string>,
    setDraft: () => {},
    send: async () => {},
    retryUnknownSend: () => {},
    retry: async () => {},
    stop: () => {},
    acceptCandidate: async () => ({}),
    adjustDraft: async () => {},
    startDraft: async () => ({}),
    abandonDraft: async () => {},
    cancelTask: async () => {},
    retryTask: async () => {},
    selectConversation: async () => {},
    bindProject: async () => {},
    continueFromMessages: async () => true,
  };
  const wrap = <T extends object>(s: T) =>
    Object.assign(
      (selector?: ((x: T) => unknown) | undefined) => (selector ? selector(s) : s),
      { getState: () => s, setState: () => {}, subscribe: () => () => {} },
    );
  return { state, useChat: wrap(state) };
});

// 手工修改会话已随 V03-10 M2 撤除，壳层不再依赖该 hook。
vi.mock("../../state/v2", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useV2Store: m.useV2,
}));
vi.mock("../../state/deskStore", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDeskStore: m.useDesk,
}));
vi.mock("../../state/chat", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatStore: c.useChat,
}));

import DeskShell from "./DeskShell";

const EMPTY_CHAPTER = {
  chapterId: null,
  baseline: null,
  draft: null,
  chapterBase: "",
  title: "",
  saving: false,
  loading: false,
  error: null,
  lastSavedAt: null,
  stale: false,
};

function seedManuscript() {
  Object.assign(m.v2, {
    currentId: "prj_seq9",
    chapters: [{ chapter_id: "ch_1", title: "第一章 雨夜", chapter_base: "base-1" }],
    chapter: {
      ...EMPTY_CHAPTER,
      chapterId: "ch_1",
      baseline: "雨下了一整夜。",
      draft: "雨下了一整夜。",
      chapterBase: "base-1",
      title: "第一章 雨夜",
    },
  });
  Object.assign(m.desk, {
    projectId: "prj_seq9",
    mainView: { kind: "manuscript", chapterId: "ch_1" },
  });
}

function seedEmptyProject() {
  Object.assign(m.v2, { currentId: "prj_seq9", chapters: [], chapter: { ...EMPTY_CHAPTER } });
  Object.assign(m.desk, { projectId: "prj_seq9", mainView: null });
}

beforeEach(() => {
  seedManuscript();
});

const MANUSCRIPT_TEXTAREA = 'aria-label="人工正文"';

describe("写作台壳层（收拢后）", () => {
  it("稿纸常驻中区且恒只读，头部无编辑入口", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain(MANUSCRIPT_TEXTAREA);
    expect(html).toMatch(/readonly/);
    expect(html).not.toContain("手工修改");
    expect(html).not.toContain("保存正文");
    expect(html).not.toContain("专注写作");
    expect(html).not.toContain("未完成的修改");
    expect(html).not.toContain("试写");
  });

  it("空态只有导入与对话两条路，无任何「试写」入口", () => {
    seedEmptyProject();
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain("还没有正文");
    expect(html).not.toContain("试写");
  });

  it("右侧固定对话栏常驻，左栏只有章节目录与对话历史", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain('aria-label="对话栏"');
    expect(html).toContain('aria-label="章节目录"');
    expect(html).toContain('aria-label="对话历史"');
    // 对话不再占用主区（V03-10 M6）：主区标题即当前章，无独立讨论主区。
    expect(html).not.toContain('aria-label="讨论"');
  });

  it("右栏默认不渲染面板，只有「设定集」常驻标记", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).not.toMatch(/role="dialog"/);
    expect(html).toContain('aria-label="侧栏标记"');
    expect(html).toContain("设定集");
    // V03-10 M3/M4：旧「账本」显示名与图谱等高级入口已撤下。
    expect(html).not.toContain("故事账本");
    expect(html).not.toContain("图谱");
    expect(html).not.toContain("整体调整");
    expect(html).not.toContain("版本历史");
  });

  it("界面无阶段编号、内部 ID、「本阶段」措辞", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).not.toMatch(/\(V03-/);
    expect(html).not.toContain("本阶段");
    expect(html).not.toContain("prj_seq9");
  });
});

describe("候选链路在写作台右侧对话栏内闭环（V03-9 §4 + V03-10 M5/M6）", () => {
  it("对话栏渲染候选卡，采用入口在对话卡内，无独立审阅视图", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain('aria-label="讨论消息"');
    expect(html).toContain("待审阅候选");
    expect(html).toContain("接受候选并提交");
    // V03-10 M5：独立审阅主区视图已撤下。
    expect(html).not.toContain("打开审阅");
  });
});

describe("失败轮次在对话栏内给出原因与重试入口", () => {
  it("失败消息显示错误三要素与「重试这一轮」；已停止的轮次不给重试", () => {
    const view = c.state.views.conv_1 as { messages: unknown[] };
    const original = view.messages;
    try {
      view.messages = [
        {
          message_id: "fail-1",
          conversation_id: "conv_1",
          turn_id: "turn_1",
          role: "assistant",
          kind: "text",
          text: "",
          refs: [],
          model: "deepseek-flash",
          status: "failed",
          error: {
            code: "CHAT_FIRST_TOKEN_TIMEOUT",
            category: "environment",
            where: "chat.runner",
            why: "模型连接建立后迟迟没有返回正文",
            fix: "检查模型端点状态后重试",
          },
          created_at: "",
        },
      ];
      const html = renderToStaticMarkup(<DeskShell />);
      expect(html).toContain("deepseek-flash");
      expect(html).toContain("模型连接建立后迟迟没有返回正文");
      expect(html).toContain("检查模型端点状态后重试");
      expect(html).toContain("重试这一轮");

      view.messages = [
        { ...(view.messages[0] as object), status: "stopped", error: null, text: "半句" },
      ];
      const stopped = renderToStaticMarkup(<DeskShell />);
      expect(stopped).toContain("以上是已收到的部分回复。");
      expect(stopped).not.toContain("重试这一轮");
    } finally {
      view.messages = original;
    }
  });
});
