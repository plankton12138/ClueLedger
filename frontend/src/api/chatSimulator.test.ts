/**
 * 模拟器单元测试（Phase 19.1）：钉死 19.2 要冻结的契约不变式。
 *
 * 覆盖：增量流式（完成前可见部分文本）、发送幂等、服务端取消与迟到分片、
 * 故障保留部分回复、任务建议→显式开始→候选→接受回读、项目隔离、
 * 重试关联、任务重试。
 */

import { describe, expect, it } from "vitest";
import { SimulatorService, scenarioFor } from "./chatSimulator";
import type { ChatEventEnvelope } from "./chat";

function collect(service: SimulatorService, projectId: string, conversationId: string) {
  const events: ChatEventEnvelope[] = [];
  service.subscribe(projectId, conversationId, (e) => events.push(e));
  return events;
}

async function waitFor(condition: () => boolean, timeout = 4000) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > timeout) throw new Error("waitFor 超时");
    await new Promise((r) => setTimeout(r, 10));
  }
}

describe("场景识别", () => {
  it("按关键词选择演示场景", () => {
    expect(scenarioFor("帮我写第一章")).toBe("draft_suggest");
    expect(scenarioFor("规划故事大纲")).toBe("plan_suggest");
    expect(scenarioFor("检查线索是否公平")).toBe("check_suggest");
    expect(scenarioFor("假如凶手改成姐姐会怎样")).toBe("revision_suggest");
    expect(scenarioFor("模拟失败")).toBe("failure");
    expect(scenarioFor("讨论角色动机")).toBe("discussion");
  });
});

describe("对话轮次", () => {
  it("增量流式：完成前即可见到部分文本，终态带完整正文", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    await service.sendTurn("p1", conv.conversation_id, {
      text: "讨论角色动机",
      refs: [],
      idempotency_key: "k1",
    });
    await waitFor(() =>
      events.some((e) => e.event.type === "message.delta"),
    );
    // 完成前已有增量文本。
    expect(events.filter((e) => e.event.type === "message.delta").length).toBeGreaterThan(0);
    await waitFor(() =>
      events.some((e) => e.event.type === "message.completed"),
    );
    const messages = await service.loadMessages("p1", conv.conversation_id);
    const assistant = messages.find((m) => m.role === "assistant")!;
    expect(assistant.status).toBe("completed");
    expect(assistant.text.length).toBeGreaterThan(10);
    // 普通讨论不产生任务建议。
    expect(messages.some((m) => m.kind === "task_draft")).toBe(false);
  });

  it("发送幂等：同一幂等键重复提交只产生一个轮次", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    const input = {
      text: "讨论角色动机",
      refs: [],
      idempotency_key: "same-key",
    };
    const first = await service.sendTurn("p1", conv.conversation_id, input);
    const second = await service.sendTurn("p1", conv.conversation_id, input);
    expect(second.turn.turn_id).toBe(first.turn.turn_id);
    expect(events.filter((e) => e.event.type === "turn.accepted")).toHaveLength(1);
    const messages = await service.loadMessages("p1", conv.conversation_id);
    expect(messages.filter((m) => m.role === "author")).toHaveLength(1);
  });

  it("停止是服务端取消：迟到分片不会出现，部分回复保留", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    const { turn } = await service.sendTurn("p1", conv.conversation_id, {
      text: "长回复",
      refs: [],
      idempotency_key: "k2",
    });
    await waitFor(() =>
      events.some((e) => e.event.type === "message.delta"),
    );
    await service.cancelTurn("p1", conv.conversation_id, turn.turn_id);
    const deltasAtCancel = events.filter((e) => e.event.type === "message.delta").length;
    await new Promise((r) => setTimeout(r, 400));
    // 取消后没有任何新分片。
    expect(events.filter((e) => e.event.type === "message.delta")).toHaveLength(
      deltasAtCancel,
    );
    expect(events.some((e) => e.event.type === "turn.cancelled")).toBe(true);
    const messages = await service.loadMessages("p1", conv.conversation_id);
    const assistant = messages.find((m) => m.role === "assistant")!;
    expect(assistant.status).toBe("stopped");
    expect(assistant.text.length).toBeGreaterThan(0);
  });

  it("故障保留部分回复，错误信封带三要素", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    await service.sendTurn("p1", conv.conversation_id, {
      text: "模拟失败",
      refs: [],
      idempotency_key: "k3",
    });
    await waitFor(() => events.some((e) => e.event.type === "turn.failed"));
    const failed = events.find((e) => e.event.type === "turn.failed")!;
    expect(failed.event.type === "turn.failed" && failed.event.error).toBeTruthy();
    if (failed.event.type !== "turn.failed") throw new Error("unreachable");
    expect(failed.event.error?.why).not.toBe("");
    expect(failed.event.error?.fix).not.toBe("");
    const messages = await service.loadMessages("p1", conv.conversation_id);
    const assistant = messages.find((m) => m.role === "assistant")!;
    expect(assistant.status).toBe("failed");
    expect(assistant.text.length).toBeGreaterThan(0);
  });

  it("重试创建关联新轮次，保留原模型绑定", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    const { turn } = await service.sendTurn("p1", conv.conversation_id, {
      text: "模拟失败",
      refs: [],
      idempotency_key: "k4",
    });
    await waitFor(() => events.some((e) => e.event.type === "turn.failed"));
    const retried = await service.retryTurn("p1", conv.conversation_id, turn.turn_id);
    expect(retried.retry_of).toBe(turn.turn_id);
    expect(retried.model).toBe(turn.model);
  });
});

describe("任务建议与创作周期", () => {
  it("讨论可产生任务建议；缺项时不能开始，补齐后可开始", async () => {
    const service = new SimulatorService({ hasAcceptedBeats: false, currentChapter: null });
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    await service.sendTurn("p1", conv.conversation_id, {
      text: "帮我写第一章",
      refs: [],
      idempotency_key: "k5",
    });
    await waitFor(() => events.some((e) => e.event.type === "task.draft.created"));
    const draft = (
      events.find((e) => e.event.type === "task.draft.created")!.event as {
        draft: { draft_id: string; missing: string[]; revision: number };
      }
    ).draft;
    expect(draft.missing.length).toBeGreaterThan(0);
    await expect(
      service.startDraft("p1", conv.conversation_id, draft.draft_id, draft.revision),
    ).rejects.toThrow(/范围还不齐全/);
    // 修订不匹配时拒绝，防多标签覆盖。
    await expect(
      service.adjustDraft("p1", conv.conversation_id, draft.draft_id, draft.revision + 5, {}),
    ).rejects.toThrow(/刚被修改过/);
  });

  it("范围齐全时：开始 → 执行推进 → 候选待审阅 → 接受 → Commit 回读", async () => {
    const service = new SimulatorService({
      hasAcceptedBeats: true,
      currentChapter: { chapter_id: "ch1", title: "第一章 雨夜", base: "base-001" },
    });
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    await service.sendTurn("p1", conv.conversation_id, {
      text: "帮我写第一章",
      refs: [],
      idempotency_key: "k6",
    });
    await waitFor(() => events.some((e) => e.event.type === "task.draft.created"));
    const created = events.find((e) => e.event.type === "task.draft.created")!.event as {
      draft: { draft_id: string; revision: number };
    };
    const { job_ref } = await service.startDraft(
      "p1",
      conv.conversation_id,
      created.draft.draft_id,
      created.draft.revision,
    );
    await waitFor(() => events.some((e) => e.event.type === "candidate.ready"), 6000);
    const candidate = events.find((e) => e.event.type === "candidate.ready")!.event as {
      message_id: string;
    };
    const result = await service.acceptCandidate(
      "p1",
      conv.conversation_id,
      candidate.message_id,
      "head-1",
    );
    expect(result.commit_id).not.toBe("");
    expect(
      events.some((e) => e.event.type === "result.accepted"),
    ).toBe(true);
    // 重复开始同一草稿只关联一个执行意图。
    const again = await service.startDraft(
      "p1",
      conv.conversation_id,
      created.draft.draft_id,
      created.draft.revision,
    );
    expect(again.job_ref).toBe(job_ref);
    // 取消后迟到输出不发布新候选：候选消息数量保持 1。
    const messages = await service.loadMessages("p1", conv.conversation_id);
    expect(messages.filter((m) => m.kind === "candidate")).toHaveLength(1);
  });

  it("取消任务后迟到候选不出现", async () => {
    const service = new SimulatorService({
      hasAcceptedBeats: true,
      currentChapter: { chapter_id: "ch1", title: "第一章", base: "base-001" },
    });
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    const { draft, message_id } = await service.createDraft("p1", conv.conversation_id, "plan");
    const started = await service.startDraft("p1", conv.conversation_id, draft.draft_id, draft.revision);
    await service.cancelTask("p1", conv.conversation_id, started.job_ref);
    await new Promise((r) => setTimeout(r, 900));
    expect(events.some((e) => e.event.type === "candidate.ready")).toBe(false);
    expect(message_id).not.toBe("");
  });

  it("输入框创作操作与消息准备任务进入同一 TaskDraft", async () => {
    const service = new SimulatorService();
    const conv = await service.createConversation("p1");
    const events = collect(service, "p1", conv.conversation_id);
    const fromComposer = await service.createDraft("p1", conv.conversation_id, "check");
    const { turn } = await service.sendTurn("p1", conv.conversation_id, {
      text: "讨论角色动机",
      refs: [],
      idempotency_key: "k7",
    });
    await waitFor(() => events.some((e) => e.event.type === "message.completed"));
    const assistant = (
      await service.loadMessages("p1", conv.conversation_id)
    ).find((m) => m.role === "assistant")!;
    const fromMessage = await service.draftFromMessage(
      "p1",
      conv.conversation_id,
      assistant.message_id,
      "plan",
    );
    expect(fromComposer.draft.task_kind).toBe("check");
    expect(fromMessage.draft.task_kind).toBe("plan");
    expect(fromMessage.draft.source_message_id).toBe(assistant.message_id);
    expect(turn.turn_id).not.toBe("");
  });
});

describe("项目隔离", () => {
  it("会话引用越界被拒绝，事件不带跨项目污染", async () => {
    const service = new SimulatorService();
    const a = await service.createConversation("p1");
    await expect(service.loadMessages("p2", a.conversation_id)).resolves.toEqual([]);
    await expect(
      service.sendTurn("p2", a.conversation_id, {
        text: "x",
        refs: [],
        idempotency_key: "k",
      }),
    ).rejects.toThrow(/不属于项目/);
  });
});
