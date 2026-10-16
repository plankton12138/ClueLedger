import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchModelSelection, selectModel } from "./models";
import { fetchProviderCatalog } from "./rest";

afterEach(() => vi.unstubAllGlobals());
function respond(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

describe("旧后端不能静默显示为空目录", () => {
  it("厂商目录缺少分组字段时明确提示重启后端", async () => {
    respond({ catalog: [{ name: "deepseek", models: ["deepseek-chat"] }] });
    await expect(fetchProviderCatalog()).rejects.toThrow("重启后端");
  });
  it("缺少模型选择读接口时提示版本不匹配", async () => {
    respond({ detail: "Not Found" }, 404);
    await expect(fetchModelSelection()).rejects.toThrow("前后端版本不匹配");
  });
  it("模型选择写请求落到静态服务时提示重启后端", async () => {
    respond({ detail: "Method Not Allowed" }, 405);
    await expect(selectModel("ollama", "qwen3.5:4b")).rejects.toThrow(
      "重启后端",
    );
  });
  it("合法空目录与服务端业务错误保持原语义", async () => {
    respond({ catalog: [] });
    await expect(fetchProviderCatalog()).resolves.toEqual([]);
    respond({ detail: "模型来源缺少可用密钥" }, 422);
    await expect(selectModel("cloud", "x")).rejects.toThrow(
      "模型来源缺少可用密钥",
    );
  });
});
