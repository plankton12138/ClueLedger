/**
 * store 快照重放剧本测试（R3 评审问题 1/2）：
 * 重连时 loadSnapshot 必须替换而非追加（防对话重复），running 以快照为唯一事实来源。
 */

import { beforeEach, describe, expect, it } from "vitest";
import type { AgentMessageWire } from "../api/types";
import { initialUiState } from "./reducer";
import { useChatStore } from "./store";

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
    status: "closed",
    connectionError: null,
    snapshot: null,
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
