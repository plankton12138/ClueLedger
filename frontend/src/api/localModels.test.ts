/**
 * 本地模型任务客户端纯函数测试（V03-1.4）。
 *
 * 钉住的关键点：
 * - isTerminalState：五态里只有三个终态——queued/running 必须继续轮询；
 * - taskNotice：成功按动作区分文案（download 强调「下载完成 ≠ 测试通过」）、
 *   failed 优先 error（三要素归因）、stopped 用 detail（取消范围）。
 */

import { describe, expect, it } from "vitest";

import { isTerminalState, taskNotice, type LocalModelTaskWire } from "./localModels";

function task(overrides: Partial<LocalModelTaskWire> = {}): LocalModelTaskWire {
  return {
    task_id: "lmt_1",
    request_id: "req_1",
    action: "download",
    model: "qwen3:4b",
    provider: "ollama",
    base_url: "http://localhost:11434",
    state: "running",
    stage: "下载中",
    percent: 42,
    detail: "",
    error: null,
    created_at: "2026-10-17T00:00:00",
    updated_at: "2026-10-17T00:00:01",
    ...overrides,
  };
}

// --- isTerminalState ---

describe("isTerminalState", () => {
  it("treats only succeeded/failed/stopped as terminal", () => {
    expect(isTerminalState(task({ state: "succeeded" }))).toBe(true);
    expect(isTerminalState(task({ state: "failed" }))).toBe(true);
    expect(isTerminalState(task({ state: "stopped" }))).toBe(true);
  });

  it("keeps queued/running polling", () => {
    expect(isTerminalState(task({ state: "queued" }))).toBe(false);
    expect(isTerminalState(task({ state: "running" }))).toBe(false);
  });
});

// --- taskNotice ---

describe("taskNotice", () => {
  it("download success warns that download is not a passing test", () => {
    const notice = taskNotice(task({ state: "succeeded" }));
    expect(notice.kind).toBe("ok");
    expect(notice.text).toContain("下载完成不等于");
  });

  it("test success points to enabling the model", () => {
    const notice = taskNotice(
      task({ state: "succeeded", action: "test", detail: "测试调用通过" }),
    );
    expect(notice.kind).toBe("ok");
    expect(notice.text).toContain("测试调用通过");
  });

  it("failed prefers the error attribution over detail", () => {
    const notice = taskNotice(
      task({
        state: "failed",
        detail: "背景归因",
        error: "Ollama 不可达。怎样算对：启动 ollama serve。",
      }),
    );
    expect(notice.kind).toBe("error");
    expect(notice.text).toContain("Ollama 不可达");
  });

  it("failed falls back to detail when error is absent", () => {
    const notice = taskNotice(task({ state: "failed", detail: "拉取失败" }));
    expect(notice.kind).toBe("error");
    expect(notice.text).toBe("拉取失败");
  });

  it("stopped surfaces the honest cancel scope", () => {
    const notice = taskNotice(
      task({ state: "stopped", stage: "已取消", detail: "取消仅限本应用发起" }),
    );
    expect(notice.kind).toBe("ok");
    expect(notice.text).toContain("仅限本应用发起");
  });
});
