/**
 * 整体调整客户端契约：任务读取、执行一轮的请求形状、继续/发布与错误信封。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  continueTask,
  fetchCurrentTask,
  publishTask,
  runRound,
  startTask,
} from "./revision";
import { V2Error } from "./v2";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("revision client", () => {
  it("没有任务时返回 null，而不是抛错", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ task_id: null, opened: false })));
    expect(await fetchCurrentTask("p1")).toBeNull();
  });

  it("执行一轮把要求、范围与幂等 id 一起发出", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        status: "stopped",
        task_id: "rtk_1",
        request_id: "rn_1",
        chapters: [],
        stopped: { reason: "budget_exhausted", chapter_id: "ch_1" },
        task: { task_id: "rtk_1", state: "waiting", budget: { calls_used: 1 } },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await runRound("p1", "rtk_1", {
      instruction: "写得更克制",
      chapter_ids: ["ch_1", "ch_2"],
      request_id: "rn_1",
    });
    expect(result.status).toBe("stopped");
    expect(result.stopped?.reason).toBe("budget_exhausted");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v2/projects/p1/revision-tasks/rtk_1/run");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      instruction: "写得更克制",
      chapter_ids: ["ch_1", "ch_2"],
      request_id: "rn_1",
    });
  });

  it("继续只追加次数，不清零已用", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        task_id: "rtk_1",
        state: "running",
        budget: { call_limit: 2, calls_used: 1, remaining: 1 },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const task = await continueTask("p1", "rtk_1", 1);
    expect(task.budget.calls_used).toBe(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/revision-tasks/rtk_1/continue");
    expect(JSON.parse(String(init.body))).toEqual({ additional_calls: 1 });
  });

  it("开任务带预算，发布带 op_ 前缀的操作号", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/publish")
        ? jsonResponse({
            status: "committed",
            operation_id: "op_1",
            commit_id: "cmt_2",
            changed_chapters: ["ch_1"],
          })
        : jsonResponse({
            task_id: "rtk_1",
            state: "queued",
            budget: { call_limit: 3, calls_used: 0 },
          }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const task = await startTask("p1", { request_id: "rq_1", call_budget: 3 });
    expect(task.budget.call_limit).toBe(3);
    const published = await publishTask("p1", "rtk_1", "op_ui_1");
    expect(published.changed_chapters).toEqual(["ch_1"]);
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toContain("/publish");
    expect(JSON.parse(String(init.body))).toEqual({ operation_id: "op_ui_1" });
  });

  it("错误信封原样透出 why 与 fix", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: "REVISION_TASK_NOT_RUNNING",
              category: "violation",
              where: "revision_exec.run",
              why: "任务当前是 waiting，不能直接执行",
              fix: "作者先继续追加预算，再执行下一轮",
              details: {},
            },
          },
          400,
        ),
      ),
    );
    await expect(
      runRound("p1", "rtk_1", { instruction: "继续改", request_id: "rn_2" }),
    ).rejects.toBeInstanceOf(V2Error);
  });
});
