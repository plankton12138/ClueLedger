/**
 * Ollama 展示纯函数测试（D11）。
 *
 * V03-1.4 起下载进度改走任务记录（api/localModels.ts），原 pull 流的
 * parsePullLines/parsePullFrame/pullPercent 已随 POST /api/ollama/pull
 * 收敛删除；本文件只钉 formatBytes / uiStateFromStatus 的边界。
 */

import { describe, expect, it } from "vitest";

import { formatBytes, uiStateFromStatus } from "./ollama";

// --- formatBytes ---

describe("formatBytes", () => {
  it("formats bytes and gigabytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2_600_000_000)).toBe("2.4 GB");
  });

  it("shows dash for non-positive values", () => {
    expect(formatBytes(0)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });
});

// --- uiStateFromStatus ---

describe("uiStateFromStatus", () => {
  it("maps detection states to ui states", () => {
    expect(uiStateFromStatus("running")).toBe("ready");
    expect(uiStateFromStatus("installed_not_running")).toBe("start");
    expect(uiStateFromStatus("not_installed")).toBe("guide");
  });

  it("falls back to unknown for missing values", () => {
    expect(uiStateFromStatus(null)).toBe("unknown");
    expect(uiStateFromStatus(undefined)).toBe("unknown");
    expect(uiStateFromStatus("weird")).toBe("unknown");
  });
});
