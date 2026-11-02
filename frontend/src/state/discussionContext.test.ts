import { beforeEach, describe, expect, it } from "vitest";

import { useConversationsStore } from "./conversations";
import { currentDiscussionFocus, useDiscussionFocusStore } from "./discussionContext";

describe("讨论焦点草稿", () => {
  beforeEach(() => {
    useConversationsStore.setState({ selectedId: null, workspaceId: "w1" });
    useDiscussionFocusStore.setState({ entries: {} });
  });

  it("准备焦点只登记引用，不发送也不创建会话", () => {
    useDiscussionFocusStore.getState().prepare({
      focus: {
        kind: "object",
        object_kind: "node",
        object_id: "n1",
        variant_id: "main",
        story_revision: "rev_1",
      },
      label: "构思：抵达",
    });

    const state = useDiscussionFocusStore.getState();
    expect(state.entries.new?.label).toBe("构思：抵达");
    expect(currentDiscussionFocus(state)?.focus.kind).toBe("object");
  });

  it("切换会话后焦点按槽位隔离，清空不会误删另一段讨论", () => {
    useDiscussionFocusStore.getState().prepare({
      focus: { kind: "chapter", path: "正文/0001.md", digest: "d1" },
      label: "本章：第一章",
    });
    useConversationsStore.setState({ selectedId: "c1" });
    useDiscussionFocusStore.getState().prepare({
      focus: {
        kind: "selection",
        path: "正文/0001.md",
        digest: "d1",
        start: 3,
        end: 7,
        quote: "雨夜",
      },
      label: "选段：雨夜",
    });

    useDiscussionFocusStore.getState().clear("new");

    const state = useDiscussionFocusStore.getState();
    expect(state.entries.new).toBeUndefined();
    expect(state.entries.c1?.label).toBe("选段：雨夜");
  });
});
