/**
 * entries 幂等键纯函数测试（V03-1.2 契约的前端消费面）。
 *
 * 后端语义（entry_service.py）：entry_id 确定性派生作品——同一入口稳定复用；
 * request_id 按次分配、失败重试复用、成功清除。
 */

import { describe, expect, it } from "vitest";

import {
  clearPendingRequestId,
  draftFingerprint,
  ideaTitle,
  newRequestId,
  readIdeaDraft,
  readPendingRequestId,
  readStableEntryId,
  writeIdeaDraft,
  writePendingRequestId,
  type DraftIdentityStorage,
} from "./entries";

function memoryStorage(): DraftIdentityStorage & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  };
}

describe("入口身份", () => {
  it("同一存储重复读取返回同一 entry_id（同一入口 = 同一作品）", () => {
    const storage = memoryStorage();
    const first = readStableEntryId(storage);
    expect(first.startsWith("ent_")).toBe(true);
    expect(readStableEntryId(storage)).toBe(first);
  });

  it("存储禁用时返回会话内身份，不抛错", () => {
    const broken: DraftIdentityStorage = {
      getItem: () => {
        throw new Error("disabled");
      },
      setItem: () => {
        throw new Error("disabled");
      },
      removeItem: () => {
        throw new Error("disabled");
      },
    };
    expect(readStableEntryId(broken).startsWith("ent_")).toBe(true);
    expect(() => readStableEntryId(broken)).not.toThrow();
  });
});

describe("request_id 按次分配", () => {
  it("首次提交无待重试键；写入后重试读到同一 request_id", () => {
    const storage = memoryStorage();
    const text = "模糊构想：两姐妹在旅馆。";
    expect(readPendingRequestId(text, storage)).toBeNull();
    const requestId = newRequestId();
    writePendingRequestId(text, requestId, storage);
    expect(readPendingRequestId(text, storage)).toBe(requestId);
  });

  it("文本变化即新键：改动后的草稿不命中旧 request_id", () => {
    const storage = memoryStorage();
    writePendingRequestId("模糊构想：两姐妹在旅馆。", newRequestId(), storage);
    expect(readPendingRequestId("模糊构想：旅馆里再加一条线索。", storage)).toBeNull();
  });

  it("保存成功清除键：同文本再保存是第二次想法", () => {
    const storage = memoryStorage();
    const text = "模糊构想：两姐妹在旅馆。";
    writePendingRequestId(text, newRequestId(), storage);
    clearPendingRequestId(text, storage);
    expect(readPendingRequestId(text, storage)).toBeNull();
  });

  it("幂等键按首尾空白归一：粘贴时带换行不影响重试命中", () => {
    const storage = memoryStorage();
    writePendingRequestId("模糊构想：两姐妹在旅馆。", newRequestId(), storage);
    expect(readPendingRequestId("  模糊构想：两姐妹在旅馆。\n", storage)).not.toBeNull();
  });

  it("存储禁用时读写不抛错（退化为失去重试幂等）", () => {
    const broken: DraftIdentityStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("disabled");
      },
      removeItem: () => {
        throw new Error("disabled");
      },
    };
    expect(() => writePendingRequestId("任意", newRequestId(), broken)).not.toThrow();
    expect(readPendingRequestId("任意", broken)).toBeNull();
  });
});

describe("辅助派生", () => {
  it("ideaTitle 取首行前 12 字；空文本回落「新想法」", () => {
    expect(ideaTitle("模糊构想：两姐妹在旅馆。\n第二条")).toBe("模糊构想：两姐妹在旅馆。");
    expect(ideaTitle("   ")).toBe("新想法");
  });

  it("draftFingerprint 同文本同指纹、异文本异指纹", () => {
    expect(draftFingerprint("姐妹旅馆")).toBe(draftFingerprint("姐妹旅馆"));
    expect(draftFingerprint("姐妹旅馆")).not.toBe(draftFingerprint("雪山别墅"));
  });

  it("草稿文本写入读取往返；空串清除", () => {
    const storage = memoryStorage();
    writeIdeaDraft("第二条想法", storage);
    expect(readIdeaDraft(storage)).toBe("第二条想法");
    writeIdeaDraft("", storage);
    expect(readIdeaDraft(storage)).toBe("");
  });
});
