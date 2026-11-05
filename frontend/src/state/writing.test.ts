/**
 * 写作页状态：草稿按章节分开、刷新恢复、摘要冲突与保存回写。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  readWorkbench: vi.fn(),
  saveChapterProse: vi.fn(),
}));

vi.mock("../api/writing", async () => {
  const actual = await vi.importActual<typeof import("../api/writing")>("../api/writing");
  return { ...actual, readWorkbench: api.readWorkbench, saveChapterProse: api.saveChapterProse };
});

import { WorkspaceApiError } from "../api/workspace";
import type { WritingWorkbenchView } from "../api/writing";
import { useWritingStore, writingDraftKey } from "./writing";

const INDEX = {
  index_revision: "idx_1",
  plan_revision: "plan_1",
  context_revision: "ctx_1",
  prose_digest: "d1",
  analysis: { state: "not_needed" as const, based_on_digest: null, operation_id: null },
  items: [],
};

function workbench(text = "初始正文", digest = "d1"): WritingWorkbenchView {
  return {
    workspace_id: "ws1",
    selection: { kind: "chapter", chapter_id: "ch1" },
    plan_revision: "plan_1",
    book: {
      structure_exists: true,
      chapters: [{
        id: "ch1", order: 0, title: "第一章", summary: "发现钥匙", prose_path: "正文/0001.md",
      }],
      information_threads: [],
      unplanned_prose: [],
      clues: { entries: [], plans: [], findings: [] },
    },
    chapter: {
      plan: {
        id: "ch1", order: 0, title: "第一章", moves: [], prose_path: "正文/0001.md",
      },
      prose: { path: "正文/0001.md", digest, text, editable: false },
      index: INDEX,
      clues: null,
    },
    model_support: { available: false, disabled_reason: "没有模型", supported_assists: [] },
    view_revision: "view_1",
  };
}

let storage: Map<string, string>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  });
  useWritingStore.getState().reset();
  vi.clearAllMocks();
  api.readWorkbench.mockResolvedValue(workbench());
});

afterEach(() => {
  useWritingStore.getState().reset();
  vi.unstubAllGlobals();
});

describe("草稿与保存", () => {
  it("按章节保存草稿，保存成功后回写摘要并在刷新时沿用本地文字", async () => {
    const store = useWritingStore.getState();
    await store.load("ws1", { chapterId: "ch1" });
    const key = writingDraftKey("ws1", "ch1");
    expect(useWritingStore.getState().drafts[key]).toMatchObject({ text: "初始正文", dirty: false });

    useWritingStore.getState().updateText("ws1", "ch1", "本地修改");
    expect(useWritingStore.getState().drafts[key]).toMatchObject({ text: "本地修改", dirty: true });

    api.saveChapterProse.mockResolvedValue({
      path: "正文/0001.md", prose_digest: "d2", plan_revision: "plan_2",
      context_revision: "ctx_2", index_revision: "idx_2", index: { ...INDEX, prose_digest: "d2" },
      analysis_job_id: null, committed_change_id: "change_1",
    });
    expect(await useWritingStore.getState().save("ws1", "ch1")).toBe(true);

    const call = api.saveChapterProse.mock.calls[0];
    expect(call?.[2]).toMatchObject({
      text: "本地修改",
      expected_digest: "d1",
    });
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "本地修改",
      baseDigest: "d2",
      dirty: false,
    });
    expect(useWritingStore.getState().workbench?.chapter?.prose?.digest).toBe("d2");
    expect(storage.size).toBe(0);
  });

  it("摘要冲突保留本地文字，载入服务端版本后清除冲突", async () => {
    const store = useWritingStore.getState();
    await store.load("ws1", { chapterId: "ch1" });
    useWritingStore.getState().updateText("ws1", "ch1", "我的修改");
    api.saveChapterProse.mockRejectedValue(new WorkspaceApiError(
      "PROSE_DIGEST_MISMATCH", "正文已被修改", "比较后再保存", 409, "正文/0001.md",
      { current_text: "服务端修改", current_digest: "server_digest" },
    ));

    expect(await useWritingStore.getState().save("ws1", "ch1")).toBe(false);
    const key = writingDraftKey("ws1", "ch1");
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "我的修改",
      dirty: true,
      conflict: { serverText: "服务端修改", serverDigest: "server_digest" },
    });

    api.readWorkbench.mockResolvedValue(workbench("服务端修改", "server_digest"));
    await useWritingStore.getState().adoptServer("ws1", "ch1");
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "服务端修改",
      dirty: false,
      conflict: null,
    });
  });
});

describe("刷新恢复", () => {
  it("恢复本地存储里的未保存文字；服务端摘要不同则进入冲突", async () => {
    const key = writingDraftKey("ws1", "ch1");
    storage.set(`hastings.writing.draft.ws1.ch1`, JSON.stringify({
      text: "刷新前的草稿", baseText: "初始正文", baseDigest: "d1",
    }));
    await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "刷新前的草稿",
      dirty: true,
      conflict: null,
    });

    useWritingStore.getState().reset();
    storage.set(`hastings.writing.draft.ws1.ch1`, JSON.stringify({
      text: "旧设备草稿", baseText: "旧正文", baseDigest: "old_digest",
    }));
    await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "旧设备草稿",
      dirty: false,
      conflict: { serverText: "初始正文", serverDigest: "d1" },
    });
  });
});

describe("稿纸修订草稿", () => {
  it("修订模式写入的草稿标记为等待确认，保存后清掉标记并记下修订", async () => {
    await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
    useWritingStore.getState().updateText("ws1", "ch1", "亲手改的一段", false, true);
    const key = writingDraftKey("ws1", "ch1");
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      dirty: true,
      confirmRequired: true,
    });
    expect(JSON.parse(storage.get(`hastings.writing.draft.ws1.ch1`) ?? "{}")).toMatchObject({
      confirmRequired: true,
    });

    api.saveChapterProse.mockResolvedValue({
      path: "正文/0001.md", prose_digest: "d2", plan_revision: "plan_2",
      context_revision: "ctx_2", index_revision: "idx_2", index: { ...INDEX, prose_digest: "d2" },
      analysis_job_id: null, committed_change_id: "change_1", revision_id: "rev_1",
    });
    expect(await useWritingStore.getState().save("ws1", "ch1")).toBe(true);
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      dirty: false,
      confirmRequired: false,
      lastRevisionId: "rev_1",
    });
  });

  it("刷新后恢复的待确认草稿不会自动入稿", async () => {
    storage.set(`hastings.writing.draft.ws1.ch1`, JSON.stringify({
      text: "刷新前没确认的修订",
      baseText: "初始正文",
      baseDigest: "d1",
      confirmRequired: true,
    }));
    vi.useFakeTimers();
    try {
      await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
      vi.advanceTimersByTime(2000);
      expect(api.saveChapterProse).not.toHaveBeenCalled();
      expect(useWritingStore.getState().drafts[writingDraftKey("ws1", "ch1")]).toMatchObject({
        text: "刷新前没确认的修订",
        dirty: true,
        confirmRequired: true,
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
