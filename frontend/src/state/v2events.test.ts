import { describe, expect, it } from "vitest";
import { advanceCursor } from "./v2events";
describe("V2 事件补齐游标", () => {
  it("同序号重放与乱序帧不重复刷新", () => {
    expect(advanceCursor(7, { type: "event", sequence: 7 })).toEqual({
      cursor: 7,
      refresh: false,
    });
    expect(advanceCursor(7, { type: "event", sequence: 6 })).toEqual({
      cursor: 7,
      refresh: false,
    });
    expect(advanceCursor(7, { type: "event", sequence: 8 })).toEqual({
      cursor: 8,
      refresh: true,
    });
  });
  it("握手只接收合法游标，契约外帧不推进", () => {
    expect(advanceCursor(0, { type: "replayed", up_to_seq: 9 }).cursor).toBe(9);
    expect(advanceCursor(9, { type: "event", sequence: "10" })).toEqual({
      cursor: 9,
      refresh: false,
    });
  });
});
