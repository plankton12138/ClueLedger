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
  chapter_revision: "chrev_1",
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
      intent: "起点",
      chapters: [{
        id: "ch1", order: 0, title: "第一章", intent: "发现钥匙", summary: "发现钥匙",
        revision: "chrev_1", confirmed: true, prose_path: "正文/0001.md", can_start: false,
      }],
      information_threads: [],
      unplanned_prose: [],
    },
    chapter: {
      plan: {
        id: "ch1", order: 0, title: "第一章", intent: "发现钥匙", summary: "发现钥匙",
        information_move_ids: [], moves: [], revision: "chrev_1", confirmed_revision: "chrev_1",
        prose_path: "正文/0001.md",
      },
      can_start: false,
      prose: { path: "正文/0001.md", digest, text, editable: true, blocked_reason: null },
      index: INDEX,
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
      path: "正文/0001.md", prose_digest: "d2", plan_revision: "plan_2", chapter_revision: "chrev_1",
      context_revision: "ctx_2", index_revision: "idx_2", index: { ...INDEX, prose_digest: "d2" },
      analysis_job_id: null, committed_change_id: "change_1",
    });
    expect(await useWritingStore.getState().save("ws1", "ch1")).toBe(true);

    const call = api.saveChapterProse.mock.calls[0];
    expect(call?.[2]).toMatchObject({
      text: "本地修改",
      expected_digest: "d1",
      expected_chapter_revision: "chrev_1",
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
      text: "刷新前的草稿", baseText: "初始正文", baseDigest: "d1", chapterRevision: "chrev_1",
    }));
    await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "刷新前的草稿",
      dirty: true,
      conflict: null,
    });

    useWritingStore.getState().reset();
    storage.set(`hastings.writing.draft.ws1.ch1`, JSON.stringify({
      text: "旧设备草稿", baseText: "旧正文", baseDigest: "old_digest", chapterRevision: "chrev_1",
    }));
    await useWritingStore.getState().load("ws1", { chapterId: "ch1" });
    expect(useWritingStore.getState().drafts[key]).toMatchObject({
      text: "旧设备草稿",
      dirty: false,
      conflict: { serverText: "初始正文", serverDigest: "d1" },
    });
  });
});
