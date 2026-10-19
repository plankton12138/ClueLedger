/**
 * 意图区分契约与展示口径（V03-7.1）。
 *
 * 覆盖两件事：
 * - HTTP 客户端形状：`POST /conversations/{id}/intents`，text 预览与 message_id 二选一；
 * - 纯函数展示口径：讨论/候选恒为只读，执行才给功能与写范围，同范围已授权不重复审批。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canWriteFormalContent,
  classifyIntent,
  intentFunctionLabel,
  intentSummary,
  needsScopeChoice,
  reusesAuthorization,
  type IntentPayload,
} from "./intent";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function payload(patch: Partial<IntentPayload>): IntentPayload {
  return {
    intent: "discussion",
    function: null,
    analysis_scope: [],
    write_scope: null,
    requires_choice: false,
    choices: [],
    reason: "",
    source: "author_message",
    source_message_id: null,
    reuses_authorization: false,
    ...patch,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("分类客户端", () => {
  it("文本预览：POST intents，body 只带 text", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v2/projects/prj_1/conversations/cv_1/intents");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({ text: "假如凶手换了会怎样" });
      return jsonResponse(payload({ intent: "discussion" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await classifyIntent("prj_1", "cv_1", { text: "假如凶手换了会怎样" });
    expect(result.intent).toBe("discussion");
  });

  it("已保存消息：body 用 message_id（不给空 text 键）", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({ message_id: "m_9" });
      return jsonResponse(
        payload({ intent: "execution", function: "revision", write_scope: ["ch1"] }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await classifyIntent("prj_1", "cv_1", { messageId: "m_9" });
    expect(result.write_scope).toEqual(["ch1"]);
  });

  it("错误信封四要素原样透出，不吞码", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: "CHAT_MESSAGE_NOT_FOUND",
              category: "violation",
              where: "intent.message",
              why: "消息不是作者消息，不能作为执行授权来源",
              fix: "只有作者消息/按钮能授权执行",
              details: {},
            },
          },
          404,
        ),
      ),
    );

    await expect(classifyIntent("prj_1", "cv_1", { messageId: "m_x" })).rejects.toMatchObject({
      code: "CHAT_MESSAGE_NOT_FOUND",
      where: "intent.message",
    });
  });
});

describe("展示口径纯函数", () => {
  it("讨论与候选都说明「不改正式内容」，且不携带写范围", () => {
    const discussion = payload({ intent: "discussion" });
    const candidate = payload({ intent: "candidate", function: "draft" });
    expect(intentSummary(discussion)).toBe("只回应，不改正式内容");
    expect(intentSummary(candidate)).toBe("生成可比较的方案，不改正式内容");
    expect(canWriteFormalContent(discussion)).toBe(false);
    expect(canWriteFormalContent(candidate)).toBe(false);
  });

  it("执行：范围已知时给出功能与写范围", () => {
    const execution = payload({
      intent: "execution",
      function: "revision",
      write_scope: ["ch1", "ch2"],
    });
    expect(intentSummary(execution)).toBe("定向修订：写范围 ch1、ch2");
    expect(canWriteFormalContent(execution)).toBe(true);
    expect(needsScopeChoice(execution)).toBe(false);
  });

  it("执行：范围未知时说明待选定的关键选择数", () => {
    const execution = payload({
      intent: "execution",
      function: "holistic_revision",
      write_scope: [],
      requires_choice: true,
      choices: [
        { where: "intent.scope", question: "覆盖哪些内容？", options: ["当前章"] },
        { where: "intent.preserve", question: "保留什么？", options: ["背景与视角"] },
      ],
    });
    expect(intentSummary(execution)).toBe("写范围待选定（2 个关键选择）");
    expect(needsScopeChoice(execution)).toBe(true);
    expect(canWriteFormalContent(execution)).toBe(false);
  });

  it("执行：同范围已授权时不重复审批", () => {
    const execution = payload({
      intent: "execution",
      function: "revision",
      write_scope: ["ch1"],
      reuses_authorization: true,
    });
    expect(intentSummary(execution)).toBe("定向修订：同范围已授权，无需重复审批");
    expect(reusesAuthorization(execution)).toBe(true);
    // 复用只针对执行；讨论/候选即便服务端误置字段也不算复用。
    expect(reusesAuthorization(payload({ intent: "candidate" }))).toBe(false);
  });

  it("执行功能中文口径", () => {
    expect(intentFunctionLabel("holistic_revision")).toBe("整体调整");
    expect(intentFunctionLabel("revision")).toBe("定向修订");
    expect(intentFunctionLabel("draft")).toBe("生成候选");
    expect(intentFunctionLabel(null)).toBe("");
  });
});
