/**
 * 对话 HTTP 客户端契约测试（Phase 19.2）。
 *
 * 覆盖计划 §19.2「API 类型、错误类型」与 §6.2 端点表、§6.3 流式事件：
 * - 端点、方法与载荷形状（含幂等键、预期修订）；
 * - V2 错误信封原样透出（code/where/why/fix 不被吞）；
 * - 能力探测：缺少新版端点 → missing_endpoints（重启指引），网络故障 → unreachable；
 * - SSE 帧解析（跨分片边界、多帧、注释心跳）与按 sequence 游标续传；
 * - 断线重连使用 `after=<最高已收序号>`，关闭后不再重连。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createHttpChatService,
  parseSseFrames,
  probeChatCapability,
  type ChatEventEnvelope,
} from "./chat";

type Calls = { url: string; method: string; body: unknown }[];

function stubFetch(
  handler: (url: string, method: string, body: unknown) => Response | Promise<Response>,
): Calls {
  const calls: Calls = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    return handler(url, method, body);
  });
  return calls;
}

const json = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const v2Error = (code: string, status: number) =>
  json(
    {
      error: {
        code,
        category: "violation",
        where: "chat.turn",
        why: "会话忙：同一会话已有进行中的轮次",
        fix: "等当前回复结束或先停止，再发送下一条",
        details: {},
      },
    },
    status,
  );

afterEach(() => vi.unstubAllGlobals());

describe("会话与轮次端点", () => {
  it("列举/新建会话使用项目前缀", async () => {
    const calls = stubFetch((url) =>
      url.endsWith("/conversations") && url.includes("/projects/p1/")
        ? json({ protocol: "chat.v1", conversations: [] })
        : json({}),
    );
    const service = createHttpChatService("p1");
    await service.listConversations("p1");
    await service.createConversation("p1");
    expect(calls[0]!.url).toBe("/api/v2/projects/p1/conversations");
    expect(calls[1]!.method).toBe("POST");
    expect(calls[1]!.url).toBe("/api/v2/projects/p1/conversations");
  });

  it("发送轮次带消息、引用与幂等键", async () => {
    const calls = stubFetch(() =>
      json({
        turn: {
          turn_id: "t1",
          conversation_id: "c1",
          status: "queued",
          model: "m",
          end_reason: null,
          idempotency_key: "idem-1",
          retry_of: null,
        },
        user_message: {
          message_id: "m1",
          conversation_id: "c1",
          turn_id: "t1",
          role: "author",
          kind: "text",
          text: "讨论动机",
          refs: [],
          model: "m",
          status: "completed",
          error: null,
          created_at: "2026-10-16T00:00:00Z",
        },
      }),
    );
    const service = createHttpChatService("p1");
    const result = await service.sendTurn("p1", "c1", {
      text: "讨论动机",
      refs: [
        {
          ref_id: "chapter:ch1",
          kind: "chapter",
          target_id: "ch1",
          title: "当前章",
          version: "v1",
          saved: true,
        },
      ],
      idempotency_key: "idem-1",
    });
    expect(calls[0]!.url).toBe("/api/v2/projects/p1/conversations/c1/turns");
    expect(calls[0]!.body).toMatchObject({
      text: "讨论动机",
      idempotency_key: "idem-1",
      refs: [{ ref_id: "chapter:ch1", saved: true }],
    });
    expect(result.turn.turn_id).toBe("t1");
    expect(result.user_message.message_id).toBe("m1");
  });

  it("停止与重试走服务端动作，不被前端本地状态代替", async () => {
    const calls = stubFetch((url) =>
      url.endsWith("/cancel")
        ? json({ status: "cancelled" })
        : json({
            turn_id: "t2",
            conversation_id: "c1",
            status: "queued",
            model: "m",
            end_reason: null,
            idempotency_key: "idem-2",
            retry_of: "t1",
          }),
    );
    const service = createHttpChatService("p1");
    await service.cancelTurn("p1", "c1", "t1");
    const retried = await service.retryTurn("p1", "c1", "t1");
    expect(calls[0]!.url).toBe("/api/v2/projects/p1/conversations/c1/turns/t1/cancel");
    expect(calls[0]!.method).toBe("POST");
    expect(calls[1]!.url).toBe("/api/v2/projects/p1/conversations/c1/turns/t1/retry");
    expect(retried.retry_of).toBe("t1");
  });

  it("任务建议：创建 / 带预期修订修改 / 显式启动", async () => {
    const calls = stubFetch((url) =>
      url.endsWith("/start")
        ? json({ job_ref: "job-1" })
        : json({
            message_id: "msg-1",
            draft: {
              draft_id: "d1",
              revision: 2,
              task_kind: "plan",
              instruction: "",
              goal: "产出故事前提与节拍候选",
              target_ids: [],
              base_version: null,
              refs: [],
              basis: [],
              missing: [],
              source_message_id: null,
              job_ref: null,
            },
          }),
    );
    const service = createHttpChatService("p1");
    await service.createDraft("p1", "c1", "plan");
    await service.adjustDraft("p1", "c1", "d1", 1, { goal: "调整目标" });
    const started = await service.startDraft("p1", "c1", "d1", 2);
    // 计划 §6.2：任务建议挂在项目下（会话由草稿的来源消息表达）。
    expect(calls[0]!.url).toBe("/api/v2/projects/p1/task-drafts");
    expect(calls[0]!.body).toMatchObject({ conversation_id: "c1", task_kind: "plan" });
    expect(calls[1]!.method).toBe("PATCH");
    expect(calls[1]!.url).toBe("/api/v2/projects/p1/task-drafts/d1");
    expect(calls[1]!.body).toMatchObject({ expected_revision: 1, goal: "调整目标" });
    expect(calls[2]!.body).toMatchObject({
      expected_revision: 2,
      idempotency_key: expect.any(String),
    });
    expect(started.job_ref).toBe("job-1");
  });

  it("拒绝时透出 V2 四要素，不吞错误码", async () => {
    stubFetch(() => v2Error("CHAT_CONVERSATION_BUSY", 409));
    const service = createHttpChatService("p1");
    await expect(
      service.sendTurn("p1", "c1", { text: "x", refs: [], idempotency_key: "k" }),
    ).rejects.toMatchObject({
      code: "CHAT_CONVERSATION_BUSY",
      fix: expect.stringContaining("停止"),
    });
  });

  it("接受候选：带基础版本与幂等键；缺基础版本时明确拒绝", async () => {
    const calls = stubFetch(() =>
      json({
        job_ref: "job-1",
        commit_id: "c-1",
        target_ids: ["ch1"],
        summary: "已提交",
      }),
    );
    const service = createHttpChatService("p1");
    const result = await service.acceptCandidate("p1", "c1", "msg-1", "head-1");
    expect(calls[0]!.url).toBe(
      "/api/v2/projects/p1/conversations/c1/messages/msg-1/accept",
    );
    expect(calls[0]!.body).toMatchObject({
      expected_head: "head-1",
      idempotency_key: expect.any(String),
    });
    expect(result.commit_id).toBe("c-1");
    await expect(
      service.acceptCandidate("p1", "c1", "msg-1", null),
    ).rejects.toMatchObject({ code: "CHAT_MISSING_BASE_VERSION" });
  });
});

describe("能力探测", () => {
  it("200 视为支持，并采用服务端协议版本", async () => {
    stubFetch(() => json({ protocol: "chat.v2", conversations: [] }));
    const probe = await probeChatCapability("p1");
    expect(probe).toMatchObject({ supported: true, protocol: "chat.v2" });
  });

  it("404/405 → missing_endpoints（引导重启后端，不显示空列表）", async () => {
    stubFetch(() => json({ detail: "Not Found" }, 404));
    expect(await probeChatCapability("p1")).toMatchObject({
      supported: false,
      reason: "missing_endpoints",
    });
    vi.unstubAllGlobals();
    stubFetch(() => json({ detail: "Method Not Allowed" }, 405));
    expect(await probeChatCapability("p1")).toMatchObject({
      supported: false,
      reason: "missing_endpoints",
    });
  });

  it("网络故障 → unreachable（可与缺少端点区分）", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await probeChatCapability("p1")).toMatchObject({
      supported: false,
      reason: "unreachable",
    });
  });
});

describe("SSE 帧解析", () => {
  it("解析多帧、跨分片边界与注释心跳", () => {
    const buffered =
      ": heartbeat\n\n" +
      'data: {"project_id":"p1","conversation_id":"c1","turn_id":"t1","sequence":1,' +
      '"event":{"type":"message.delta","turn_id":"t1","text":"甲"}}\n\n' +
      'data: {"project_id":"p1","conversation_id":"c1","turn_id":"t1","sequence":2,' +
      '"event":{"type":"message.delta","turn_id":"t1","text":"乙"}}\n\n' +
      "data: partial-frame";
    const { envelopes, rest } = parseSseFrames(buffered);
    expect(envelopes.map((e) => e.sequence)).toEqual([1, 2]);
    expect(envelopes[0]!.event).toMatchObject({ type: "message.delta", text: "甲" });
    // 未完成帧留在缓冲，等下一个分片补齐。
    expect(rest).toBe("data: partial-frame");
  });

  it("非法帧被跳过，不影响后续合法帧", () => {
    const buffered =
      "data: {oops\n\n" +
      'data: {"project_id":"p1","conversation_id":"c1","turn_id":null,"sequence":3,' +
      '"event":{"type":"task.draft.abandoned","draft_id":"d1"}}\n\n';
    const { envelopes } = parseSseFrames(buffered);
    expect(envelopes).toHaveLength(1);
    expect(envelopes[0]!.sequence).toBe(3);
  });
});

describe("流式订阅与重连", () => {
  function streamOf(frames: string[]): Response {
    const encoder = new TextEncoder();
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const frame of frames) controller.enqueue(encoder.encode(frame));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  it("按已收序号续传，重连携带 after 游标", async () => {
    const seen: number[] = [];
    const calls: string[] = [];
    let round = 0;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      calls.push(String(input));
      round += 1;
      return round === 1
        ? streamOf([
            'data: {"project_id":"p1","conversation_id":"c1","turn_id":"t1","sequence":5,' +
              '"event":{"type":"message.delta","turn_id":"t1","text":"甲"}}\n\n',
          ])
        : new Response(null, { status: 500 });
    });
    const service = createHttpChatService("p1", { retryBaseMs: 5 });
    const unsubscribe = service.subscribe(
      "p1",
      "c1",
      (envelope: ChatEventEnvelope) => seen.push(envelope.sequence),
      () => undefined,
    );
    await new Promise((r) => setTimeout(r, 60));
    unsubscribe();
    expect(seen).toEqual([5]);
    // 首连 after=0；断线重连必须带 after=5，不重复消费同一序号。
    expect(calls[0]).toContain("/events?after=0");
    expect(calls.some((u) => u.includes("/events?after=5"))).toBe(true);
  });

  it("关闭订阅后不再重连", async () => {
    let attempts = 0;
    vi.stubGlobal("fetch", async () => {
      attempts += 1;
      return new Response(null, { status: 500 });
    });
    const service = createHttpChatService("p1", { retryBaseMs: 5 });
    const unsubscribe = service.subscribe("p1", "c1", () => undefined, () => undefined);
    await new Promise((r) => setTimeout(r, 30));
    unsubscribe();
    const after = attempts;
    await new Promise((r) => setTimeout(r, 60));
    expect(attempts).toBe(after);
  });
});
