/**
 * 写作台壳层行为测试（V03-9 M8）：稿纸默认只读、手工修改才可编辑、
 * 空态无「试写」入口、右栏默认不渲染面板、界面无开发痕迹。
 *
 * 用 react-dom/server 静态渲染（项目偏好：不引入 jsdom）。zustand v5 在 SSR
 * 下以初始状态作 server snapshot，setState 不生效，故此处直接替换壳层依赖的
 * 两个 store 为可控替身；组件 effect 不执行，状态即渲染输入。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

const h = vi.hoisted(() => ({ editActive: false }));
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
  const desk = {
    projectId: "prj_seq9" as string | null,
    mainView: { kind: "manuscript", chapterId: "ch_1" } as unknown,
    readingPositions: {} as Record<string, unknown>,
    operations: {} as Record<string, unknown>,
    entryReceipts: [],
    lastOperationId: null,
    composerFocusTick: 0,
    composerInsert: null,
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

// 对话 store：注入一条候选消息，验证 §4 候选链路在写作台内的呈现接线。
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
    retryUnknownSend: () => {},
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

// 手工修改会话：默认未激活；第二条用例切到激活态验证可编辑。
vi.mock("./useEditSession", () => ({
  useEditSession: () => ({
    view: null,
    active: h.editActive,
    text: "副本中的正文",
    seq: 3,
    saving: false,
    error: null,
    conflict: false,
    start: async () => {},
    resume: () => {},
    setText: () => {},
    finish: async () => null,
    suspend: async () => {},
    abandon: async () => {},
  }),
}));
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
  h.editActive = false;
  seedManuscript();
});

const MANUSCRIPT_TEXTAREA = 'aria-label="人工正文"';

describe("写作台壳层（收拢后）", () => {
  it("稿纸默认只读，头部只给三种阅读态入口", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain(MANUSCRIPT_TEXTAREA);
    expect(html).toMatch(/readonly/);
    expect(html).toContain("手工修改");
    expect(html).not.toContain("保存正文");
    expect(html).not.toContain("专注写作");
    expect(html).not.toContain("未完成的修改");
  });

  it("手工修改激活后稿纸可编辑", () => {
    h.editActive = true;
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain(MANUSCRIPT_TEXTAREA);
    expect(html).not.toMatch(/readonly/);
    expect(html).toContain("完成修改");
  });

  it("空态只有导入与对话两条路，无任何「试写」入口", () => {
    seedEmptyProject();
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain("还没有正文");
    expect(html).not.toContain("试写");
  });

  it("右栏默认不渲染面板，只有常驻标记条", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).not.toMatch(/role="dialog"/);
    expect(html).toContain('aria-label="侧栏标记"');
    expect(html).toContain("账本");
    expect(html).toContain("图谱");
  });

  it("界面无阶段编号、内部 ID、「本阶段」措辞", () => {
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).not.toMatch(/\(V03-/);
    expect(html).not.toContain("本阶段");
    expect(html).not.toContain("prj_seq9");
  });
});

describe("候选链路在写作台内闭环（V03-9 §4）", () => {
  it("讨论区渲染候选卡，含打开审阅与接受入口", () => {
    Object.assign(m.desk, {
      projectId: "prj_seq9",
      mainView: { kind: "discussion", conversationId: "conv_1" },
    });
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain('aria-label="讨论消息"');
    expect(html).toContain("待审阅候选");
    expect(html).toContain("打开审阅");
    expect(html).toContain("接受候选并提交");
  });

  it("候选审阅在主区展开，采用与返回讨论入口齐全", () => {
    Object.assign(m.desk, {
      projectId: "prj_seq9",
      mainView: { kind: "detail", detailKind: "candidate", id: "cand-1" },
    });
    const html = renderToStaticMarkup(<DeskShell />);
    expect(html).toContain("候选审阅");
    expect(html).toContain('aria-label="候选完整内容"');
    expect(html).toContain("接受候选并提交");
    expect(html).toContain("返回讨论");
  });
});
