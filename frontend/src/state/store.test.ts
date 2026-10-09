/**
 * store 快照重放剧本测试（R3 评审问题 1/2）：
 * 重连时 loadSnapshot 必须替换而非追加（防对话重复），running 以快照为唯一事实来源。
 * P5 T5 追加 manuscript 编辑状态机与对账 notice 迁移剧本。
 */

import { beforeEach, describe, expect, it } from "vitest";
import type { DiffHunkWire } from "../api/rest";
import type { AgentMessageWire, NoticeFrame } from "../api/types";
import { initialUiState } from "./reducer";
import { initialManuscript, useChatStore } from "./store";

const SNAP_STATE = { slug: "demo", model: "fake", is_running: false, tools: [] };

const HISTORY: AgentMessageWire[] = [
  { role: "user", content: "建角色", timestamp: 1 },
  {
    role: "assistant",
    content: [{ type: "text", text: "已建立波洛" }],
    stopReason: "stop",
    timestamp: 2,
  },
];

beforeEach(() =>
  useChatStore.setState({
    ...initialUiState,
    slug: null,
    workspaces: [],
    status: "closed",
    connectionError: null,
    snapshot: null,
    manuscript: initialManuscript,
    syncCards: [],
  }),
);

describe("loadSnapshot 快照重放（R3 问题1：重连对话重复）", () => {
  it("已有上一段连接的条目时，快照替换而非追加", () => {
    useChatStore.setState({
      items: [
        { kind: "user", text: "建角色" },
        { kind: "assistant", text: "已建立波洛" },
      ],
    });
    useChatStore.getState().loadSnapshot({ ...SNAP_STATE }, HISTORY);
    const items = useChatStore.getState().items;
    expect(items.filter((i) => i.kind === "user" && i.text === "建角色")).toHaveLength(1);
    expect(items.filter((i) => i.kind === "assistant" && i.text === "已建立波洛")).toHaveLength(1);
  });

  it("重置后 eventLog 清空（每段连接一份事件日志），契约外事件报告保留", () => {
    useChatStore.setState({ eventLog: [{ type: "agent_start" }], unhandledEventTypes: ["custom_x"] });
    useChatStore.getState().loadSnapshot({ ...SNAP_STATE }, []);
    expect(useChatStore.getState().eventLog).toHaveLength(0);
    expect(useChatStore.getState().unhandledEventTypes).toEqual(["custom_x"]);
  });
});

describe("loadSnapshot running 语义（R3 问题2：卡 true）", () => {
  it("快照 is_running=false 纠正 stale running=true", () => {
    useChatStore.setState({ running: true });
    useChatStore.getState().loadSnapshot({ ...SNAP_STATE, is_running: false }, HISTORY);
    expect(useChatStore.getState().running).toBe(false);
  });

  it("快照 is_running=true 恢复写作中（页面加载时 agent 已在写作）", () => {
    useChatStore.getState().loadSnapshot({ ...SNAP_STATE, is_running: true }, []);
    expect(useChatStore.getState().running).toBe(true);
  });
});

describe("工作区切片（P4 T6）", () => {
  it("setSlug 记录当前工作区；切换不改写事件流（由快照重放整体更替）", () => {
    useChatStore.setState({ items: [{ kind: "user", text: "旧案卷的发言" }] });
    useChatStore.getState().setSlug("story-a");
    expect(useChatStore.getState().slug).toBe("story-a");
    useChatStore.getState().setSlug("story-b");
    expect(useChatStore.getState().slug).toBe("story-b");
    // 切换瞬间旧条目仍在——待新连接快照到达后由 loadSnapshot 重置（见上组测试）
    expect(useChatStore.getState().items).toHaveLength(1);
  });

  it("setWorkspaces 保存列表", () => {
    useChatStore.getState().setWorkspaces(["story-a", "story-b"]);
    expect(useChatStore.getState().workspaces).toEqual(["story-a", "story-b"]);
  });
});

// ---------- manuscript 切片（P5 T1）：章节切换加载 / 竞态防护 / 工作区重置 ----------

function openChapter(id: string, content: string, revision = 1): void {
  useChatStore.getState().selectChapter(id);
  useChatStore.getState().chapterLoaded(id, content, revision);
}

describe("manuscript 切片（P5 T1 + T2 revision）", () => {
  it("selectChapter 进入 loading；chapterLoaded 落 baseline/draft 与 revision", () => {
    useChatStore.setState({ manuscript: { ...initialManuscript, chapterIds: ["ch01"] } });
    useChatStore.getState().selectChapter("ch01");
    expect(useChatStore.getState().manuscript.loading).toBe(true);
    expect(useChatStore.getState().manuscript.revision).toBeNull();
    useChatStore.getState().chapterLoaded("ch01", "# 第1章 开端\n\n正文。", 3);
    const ms = useChatStore.getState().manuscript;
    expect(ms.baseline).toBe("# 第1章 开端\n\n正文。");
    expect(ms.draft).toBe("# 第1章 开端\n\n正文。"); // draft 初始 = baseline（未编辑）
    expect(ms.revision).toBe(3);
    expect(ms.loading).toBe(false);
    expect(ms.error).toBeNull();
  });

  it("chapterFailed 记录错误并退出 loading", () => {
    useChatStore.getState().selectChapter("ch02");
    useChatStore.getState().chapterFailed("ch02", "章节读取失败：HTTP 404");
    const ms = useChatStore.getState().manuscript;
    expect(ms.error).toBe("章节读取失败：HTTP 404");
    expect(ms.loading).toBe(false);
    expect(ms.baseline).toBeNull();
  });

  it("竞态防护：晚到的旧章响应被丢弃，不覆盖已切换的新章", () => {
    openChapter("ch01", "第一章正文");
    useChatStore.getState().selectChapter("ch02");
    // ch01 的响应在切换后才返回
    useChatStore.getState().chapterLoaded("ch01", "迟到的第一章正文", 1);
    const ms = useChatStore.getState().manuscript;
    expect(ms.chapterId).toBe("ch02");
    expect(ms.baseline).toBeNull(); // ch02 尚未返回，loading 保持
    expect(ms.loading).toBe(true);
  });

  it("失败响应同样按 id 比对：旧章的失败不污染新章", () => {
    useChatStore.getState().selectChapter("ch01");
    useChatStore.getState().selectChapter("ch02");
    useChatStore.getState().chapterFailed("ch01", "旧章错误");
    expect(useChatStore.getState().manuscript.error).toBeNull();
  });

  it("工作区切换（setSlug 到新 slug）→ manuscript 整体重置（草稿/章节不残留）", () => {
    useChatStore.setState({ slug: "story-a" });
    useChatStore.setState({
      manuscript: {
        ...initialManuscript,
        chapterIds: ["ch01", "ch02"],
        chapterId: "ch01",
        baseline: "旧案卷正文",
        draft: "旧案卷正文改",
        revision: 2,
      },
    });
    useChatStore.getState().setSlug("story-b");
    expect(useChatStore.getState().manuscript).toEqual(initialManuscript);
  });

  it("loadSnapshot（重连/重放）→ manuscript 保留（同工作区重连不丢正文与草稿；重置点唯一在 setSlug）", () => {
    openChapter("ch01", "正文");
    expect(useChatStore.getState().manuscript.baseline).not.toBeNull();
    useChatStore.getState().loadSnapshot(
      { slug: "demo", model: "fake", is_running: false, tools: [] },
      [],
    );
    const ms = useChatStore.getState().manuscript;
    expect(ms.chapterId).toBe("ch01");
    expect(ms.baseline).toBe("正文");
  });

  it("setSlug 同 slug 不重置（重复设置无害）", () => {
    useChatStore.setState({ slug: "story-a" });
    openChapter("ch01", "正文");
    useChatStore.getState().setSlug("story-a");
    expect(useChatStore.getState().manuscript.baseline).toBe("正文");
  });
});

// ---------- manuscript 编辑状态机（P5 T5）：草稿 / 保存 / 放弃 ----------

const HUNKS: DiffHunkWire[] = [
  { kind: "modified", oldIndex: 1, newIndex: 1, oldText: "旧段", newText: "新段" },
];

describe("manuscript 编辑状态机（P5 T5）", () => {
  it("setDraft：改动 → dirty，改回 baseline → clean（dirty 是派生量）", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().setDraft("# 第3章\n\n改后。");
    expect(useChatStore.getState().manuscript.saveState).toBe("dirty");
    useChatStore.getState().setDraft("# 第3章\n\n原文。");
    expect(useChatStore.getState().manuscript.saveState).toBe("clean");
  });

  it("saveStart → saving；applySaved → draft 成为 baseline、clean、进 diff 视图", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().setDraft("# 第3章\n\n改后。");
    useChatStore.getState().saveStart();
    expect(useChatStore.getState().manuscript.saveState).toBe("saving");
    useChatStore.getState().applySaved(4, HUNKS);
    const ms = useChatStore.getState().manuscript;
    expect(ms.baseline).toBe("# 第3章\n\n改后。");
    expect(ms.revision).toBe(4);
    expect(ms.saveState).toBe("clean");
    expect(ms.diff).toEqual(HUNKS);
    expect(ms.mode).toBe("diff");
  });

  it("applySaved 空 diff → 不算变更，退回阅读（无变更正常结果，非错误）", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().applySaved(1, []);
    const ms = useChatStore.getState().manuscript;
    expect(ms.diff).toBeNull();
    expect(ms.mode).toBe("read");
    expect(ms.saveState).toBe("clean");
  });

  it("saveFailed → error 状态与文案（正文草稿不丢，可重试保存）", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().setDraft("# 第3章\n\n改后。");
    useChatStore.getState().saveFailed("章节 ch03 保存失败：章节已被其他页面更新");
    const ms = useChatStore.getState().manuscript;
    expect(ms.saveState).toBe("error");
    expect(ms.error).toContain("已被其他页面更新");
    expect(ms.draft).toBe("# 第3章\n\n改后。"); // 草稿仍在，重试即可
  });

  it("discardDraft → draft 回到 baseline、清 diff、回阅读", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().setDraft("# 第3章\n\n改后。");
    useChatStore.getState().discardDraft();
    const ms = useChatStore.getState().manuscript;
    expect(ms.draft).toBe("# 第3章\n\n原文。");
    expect(ms.baseline).toBe("# 第3章\n\n原文。");
    expect(ms.saveState).toBe("clean");
    expect(ms.diff).toBeNull();
    expect(ms.mode).toBe("read");
  });
});

// ---------- 对账 notice 迁移（P5 T5）：queued → syncing → done/failed + 卡片 ----------

function notice(partial: Partial<NoticeFrame> & { kind: NoticeFrame["kind"] }): NoticeFrame {
  return { type: "notice", chapter: "ch03", hunks: 0, updates: 0, ...partial };
}

describe("applyNotice 对账迁移与同步卡片（P5 T5）", () => {
  it("当前章：queued → syncing → done 徽标迁移；sync_end 追加完成卡片", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().applyNotice(notice({ kind: "sync_queued", hunks: 3 }));
    expect(useChatStore.getState().manuscript.syncState).toBe("queued");
    useChatStore.getState().applyNotice(notice({ kind: "sync_start", hunks: 3 }));
    expect(useChatStore.getState().manuscript.syncState).toBe("syncing");
    useChatStore.getState().applyNotice(
      notice({ kind: "sync_end", hunks: 3, updates: 2, status: "done" }),
    );
    expect(useChatStore.getState().manuscript.syncState).toBe("done");
    expect(useChatStore.getState().syncCards).toEqual([
      { chapter: "ch03", hunks: 3, updates: 2, status: "done" },
    ]);
  });

  it("failed：syncState=failed；卡片 status=failed（UI 据此出重试按钮）", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().applyNotice(notice({ kind: "sync_start", hunks: 2 }));
    useChatStore.getState().applyNotice(
      notice({ kind: "sync_end", hunks: 2, updates: 0, status: "failed" }),
    );
    expect(useChatStore.getState().manuscript.syncState).toBe("failed");
    expect(useChatStore.getState().syncCards[0]?.status).toBe("failed");
  });

  it("非当前章的 notice 不改工具栏徽标，但卡片仍照记", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().applyNotice(
      notice({ kind: "sync_queued", chapter: "ch05", hunks: 1 }),
    );
    expect(useChatStore.getState().manuscript.syncState).toBe("idle");
    useChatStore.getState().applyNotice(
      notice({ kind: "sync_end", chapter: "ch05", hunks: 1, updates: 1, status: "done" }),
    );
    expect(useChatStore.getState().syncCards).toEqual([
      { chapter: "ch05", hunks: 1, updates: 1, status: "done" },
    ]);
  });

  it("snapshot 重放与工作区切换：对账卡片清空（与对话流同生命周期）", () => {
    openChapter("ch03", "# 第3章\n\n原文。");
    useChatStore.getState().applyNotice(
      notice({ kind: "sync_end", hunks: 1, updates: 1, status: "done" }),
    );
    expect(useChatStore.getState().syncCards).toHaveLength(1);

    useChatStore.getState().loadSnapshot(SNAP_STATE, []);
    expect(useChatStore.getState().syncCards).toHaveLength(0);
  });
});