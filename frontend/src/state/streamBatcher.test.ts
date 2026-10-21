import { afterEach, expect, it, vi } from "vitest";

import type { WorkspaceEvent } from "../api/workspace";
import { createStreamBatcher, STREAM_RENDER_INTERVAL } from "./streamBatcher";

const delta = (seq: number): WorkspaceEvent => ({
  seq, at: "", type: "assistant_block_delta", turn_id: "t1", message_id: "m1",
  block_id: "b1", kind: "thinking", part: seq - 1, text: `片段${seq}`,
});

afterEach(() => vi.useRealTimers());

it("一批小片段按时间发布一次，保留每个分片与原有顺序", () => {
  vi.useFakeTimers();
  const publish = vi.fn();
  const batcher = createStreamBatcher(publish);
  const events = Array.from({ length: 200 }, (_, i) => delta(i + 1));
  events.forEach(batcher.push);
  expect(publish).not.toHaveBeenCalled();
  vi.advanceTimersByTime(STREAM_RENDER_INTERVAL);
  expect(publish.mock.calls).toEqual([[events]]);
});

it("响应结束或停止立即发布缓冲，终态不会越过已有文字", () => {
  vi.useFakeTimers();
  const publish = vi.fn();
  const batcher = createStreamBatcher(publish);
  batcher.push(delta(1));
  const end: WorkspaceEvent = {
    seq: 2, at: "", type: "assistant_finished", turn_id: "t1", message_id: "m1",
    completion: "stopped",
  };
  batcher.push(end);
  expect(publish.mock.calls).toEqual([[[delta(1), end]]]);
  vi.runAllTimers();
  expect(publish).toHaveBeenCalledTimes(1);
});

it("大量突发事件有缓冲上限，结束前仍能发布全部内容", () => {
  vi.useFakeTimers();
  const batches: WorkspaceEvent[][] = [];
  const batcher = createStreamBatcher(events => batches.push(events));
  const events = Array.from({ length: 20000 }, (_, i) => delta(i + 1));
  events.forEach(batcher.push);
  expect(batches.length).toBeGreaterThan(0);
  expect(Math.max(...batches.map(batch => batch.length))).toBeLessThanOrEqual(512);
  batcher.flush();
  expect(batches.flat()).toEqual(events);
});

it("断开前提交已收到的部分，后续计时器不重复发布", () => {
  vi.useFakeTimers();
  const publish = vi.fn();
  const batcher = createStreamBatcher(publish);
  batcher.push(delta(1));
  batcher.flush();
  batcher.push(delta(2));
  vi.advanceTimersByTime(STREAM_RENDER_INTERVAL);
  expect(publish.mock.calls).toEqual([[[delta(1)]], [[delta(2)]]]);
});
