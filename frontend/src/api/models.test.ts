/**
 * 模型设置客户端（0.4 V04-9C）。
 *
 * 钉契约：写操作带 `If-Match`、错误信封转成带 code 的异常、发现与测试不改配置。
 * 真正的服务端行为由后端测试与浏览器走查负责。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createConnection,
  deleteConnection,
  discoverModels,
  ModelApiError,
  probeModel,
  readConfig,
  selectModel,
  updateConnection,
} from "./models";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DRAFT = {
  display_name: "我的连接",
  catalog_id: "deepseek",
  base_url: "https://api.deepseek.com/v1",
  api: "openai-completions" as const,
  models: ["deepseek-chat"],
  default_model: "deepseek-chat",
  api_key: "sk-test",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("连接写入", () => {
  it("新增连接带 If-Match 与完整草稿", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ revision: "r2", selection: {}, connections: [] });
    });

    await createConnection(DRAFT, "r1");

    expect(calls[0]!.url).toBe("/api/model-connections");
    expect(calls[0]!.init?.method).toBe("POST");
    const headers = calls[0]!.init?.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe("r1");
    expect(JSON.parse(String(calls[0]!.init?.body))).toMatchObject({
      base_url: "https://api.deepseek.com/v1",
      api_key: "sk-test",
      models: ["deepseek-chat"],
    });
  });

  it("编辑与删除都编码连接名并带版本", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ revision: "r2", selection: {}, connections: [] });
    });

    await updateConnection("conn 1/a", DRAFT, "r1");
    await deleteConnection("conn 1/a", "r2");

    expect(calls[0]!.url).toBe("/api/model-connections/conn%201%2Fa");
    expect(calls[0]!.init?.method).toBe("PATCH");
    expect(calls[1]!.init?.method).toBe("DELETE");
    expect((calls[1]!.init?.headers as Record<string, string>)["If-Match"]).toBe("r2");
  });

  it("选择清空时 connection_id 为 null", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return jsonResponse({ revision: "r2", selection: {}, connections: [] });
    });

    await selectModel(null, "", "r1");

    expect(bodies[0]).toEqual({ connection_id: null, model: "" });
  });
});

describe("发现与测试", () => {
  it("获取列表把草稿地址与 key 一起带上，不写配置", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      expect(url).toBe("/api/model-connections/discover");
      bodies.push(JSON.parse(String(init?.body)));
      return jsonResponse({
        revision: "r1",
        connection_id: null,
        models: ["m"],
        complete: true,
        code: null,
        message: "",
      });
    });

    const result = await discoverModels({
      catalog_id: "deepseek",
      base_url: "https://api.deepseek.com/v1",
      api: "openai-completions",
      api_key: "sk-draft",
    });

    expect(result.models).toEqual(["m"]);
    expect(bodies[0]).toMatchObject({ connection_id: null, api_key: "sk-draft" });
  });

  it("测试请求区分 response 与 tool", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return jsonResponse({
        revision: "r1",
        connection_id: null,
        model: "m",
        response: "passed",
        tool_call: "untested",
        code: null,
        message: "响应测试通过。",
      });
    });

    await probeModel({
      catalog_id: "deepseek",
      base_url: "https://api.deepseek.com/v1",
      api: "openai-completions",
      kind: "tool",
      model: "m",
    });

    expect(bodies[0]).toMatchObject({ kind: "tool", model: "m" });
  });
});

describe("错误信封", () => {
  it("409 冲突保留稳定 code 与修复建议", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        {
          error: {
            code: "MODEL_CONFIG_CONFLICT",
            where: "model_connections.save",
            why: "配置文件已被改过",
            fix: "重新读取后再提交",
          },
        },
        409,
      ),
    );

    await expect(readConfig()).rejects.toBeInstanceOf(ModelApiError);
    await expect(readConfig()).rejects.toMatchObject({
      code: "MODEL_CONFIG_CONFLICT",
      status: 409,
      where: "model_connections.save",
      fix: "重新读取后再提交",
    });
  });

  it("非 JSON 错误体不丢状态码", async () => {
    vi.stubGlobal("fetch", async () => new Response("boom", { status: 500 }));

    await expect(readConfig()).rejects.toMatchObject({ code: "HTTP_500", status: 500 });
  });
});
