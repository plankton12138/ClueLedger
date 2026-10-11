/**
 * Ollama 流解析与展示纯函数测试（D11）。
 *
 * 钉住的关键点：
 * - parsePullLines：跨 chunk 断行（一帧被切成两半）、无尾换行的残行留余量；
 * - parsePullFrame：error 帧 / 缺字段 / 坏 JSON；
 * - pullPercent：缺数字、total 非正、0-100 裁剪（进度是纯展示，服务端不裁）；
 * - formatBytes / uiStateFromStatus 的边界。
 */

import { describe, expect, it } from "vitest";

import {
  formatBytes,
  parsePullFrame,
  parsePullLines,
  pullPercent,
  uiStateFromStatus,
} from "./ollama";

// --- parsePullLines：跨 chunk 断行 ---

describe("parsePullLines", () => {
  it("splits complete lines and keeps the remainder", () => {
    const first = parsePullLines("", '{"a":1}\n{"b":2}\n{"c"');
    expect(first.lines).toEqual(['{"a":1}', '{"b":2}']);
    expect(first.buffer).toBe('{"c"');

    const second = parsePullLines(first.buffer, ':3}\n');
    expect(second.lines).toEqual(['{"c":3}']);
    expect(second.buffer).toBe("");
  });

  it("handles chunk without trailing newline", () => {
    const result = parsePullLines("", '{"status":"pulling manifest"}');
    expect(result.lines).toEqual([]);
    expect(result.buffer).toBe('{"status":"pulling manifest"}');
  });

  it("drops empty lines between frames", () => {
    const result = parsePullLines("", '{"a":1}\n\n\n{"b":2}\n');
    expect(result.lines).toEqual(['{"a":1}', '{"b":2}']);
  });
});

// --- parsePullFrame ---

describe("parsePullFrame", () => {
  it("parses progress frame with numbers", () => {
    expect(parsePullFrame('{"status":"downloading","total":100,"completed":50}')).toEqual({
      status: "downloading",
      total: 100,
      completed: 50,
    });
  });

  it("parses success terminal frame", () => {
    expect(parsePullFrame('{"status":"success"}')).toEqual({ status: "success" });
  });

  it("maps error field to error frame", () => {
    expect(parsePullFrame('{"error":"manifest not found"}')).toEqual({
      status: "",
      error: "manifest not found",
    });
  });

  it("returns null for bad json or non-status frames", () => {
    expect(parsePullFrame("not json")).toBeNull();
    expect(parsePullFrame('{"unexpected":true}')).toBeNull();
    expect(parsePullFrame("null")).toBeNull();
  });
});

// --- pullPercent ---

describe("pullPercent", () => {
  it("computes rounded percentage", () => {
    expect(pullPercent({ status: "d", total: 300, completed: 100 })).toBe(33);
  });

  it("returns null when numbers are missing or total is non-positive", () => {
    expect(pullPercent({ status: "pulling manifest" })).toBeNull();
    expect(pullPercent({ status: "d", total: 0, completed: 0 })).toBeNull();
  });

  it("clamps into 0-100", () => {
    expect(pullPercent({ status: "d", total: 100, completed: 250 })).toBe(100);
    expect(pullPercent({ status: "d", total: 100, completed: -5 })).toBe(0);
  });
});

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
