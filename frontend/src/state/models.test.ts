/**
 * 模型设置状态（0.4 V04-9C）。
 *
 * 关注四件事：写操作一次应用完整配置、冲突不丢表单依据、选择以后端返回为准、
 * Key 从不进入全局状态。API 模块整体替身，状态逻辑用真实代码。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listProviders: vi.fn(),
  listAuthProviders: vi.fn(),
  readConfig: vi.fn(),
  createConnection: vi.fn(),
  updateConnection: vi.fn(),
  deleteConnection: vi.fn(),
  selectModel: vi.fn(),
  discoverModels: vi.fn(),
  probeModel: vi.fn(),
}));

vi.mock("../api/models", async () => {
  const actual = await vi.importActual<typeof import("../api/models")>("../api/models");
  return { ...actual, ...api };
});

import { ModelApiError } from "../api/models";
import { selectionLabel, useModelsStore } from "./models";

const EMPTY = {
  revision: "r1",
  selection: {
    connection_id: null,
    model: "",
    source: "none" as const,
    available: false,
    reason: "还没有可用的模型连接",
    thinking_level: null,
    thinking_levels: [],
  },
  connections: [],
};

const CONNECTED = {
  revision: "r2",
  selection: {
    connection_id: "conn_1",
    model: "deepseek-chat",
    source: "saved" as const,
    available: true,
    reason: "",
    thinking_level: null,
    thinking_levels: [],
  },
  connections: [
    {
      name: "conn_1",
      display_name: "我的 DeepSeek",
      catalog_id: "deepseek",
      api: "openai-completions" as const,
      base_url: "https://api.deepseek.com/v1",
      models: ["deepseek-chat", "deepseek-reasoner"],
      default_model: "deepseek-chat",
      has_key: true,
      masked_key: "…1234",
      credential_storage: "system" as const,
      auth_method: "api_key" as const,
      auth_provider: "",
      account: "",
      auth_status: "ready" as const,
    },
  ],
};

const PRESETS = [
  {
    id: "deepseek",
    display_name: "DeepSeek",
    api: "openai-completions" as const,
    api_options: ["openai-completions" as const],
    base_url: "https://api.deepseek.com/v1",
    docs_url: "",
    discovery: "openai-models" as const,
    regions: [],
    auth_providers: [],
    custom: false,
    note: "",
  },
];

function reset(): void {
  useModelsStore.setState(useModelsStore.getInitialState(), true);
}

beforeEach(() => {
  reset();
  vi.clearAllMocks();
  api.readConfig.mockResolvedValue(EMPTY);
  api.listProviders.mockResolvedValue(PRESETS);
  api.listAuthProviders.mockResolvedValue([]);
});

describe("初始化与刷新", () => {
  it("只读取本机配置与预设，不触发任何供应商测试", async () => {
    await useModelsStore.getState().init();

    expect(api.readConfig).toHaveBeenCalledTimes(1);
    expect(api.listProviders).toHaveBeenCalledTimes(1);
    expect(api.discoverModels).not.toHaveBeenCalled();
    expect(api.probeModel).not.toHaveBeenCalled();
    expect(useModelsStore.getState().loaded).toBe(true);
    expect(useModelsStore.getState().presets).toHaveLength(1);
  });

  it("初始化时加载可登录的订阅来源", async () => {
    api.listAuthProviders.mockResolvedValue([{ id: "chatgpt", name: "ChatGPT 订阅" }]);

    await useModelsStore.getState().init();

    expect(useModelsStore.getState().authProviders).toEqual([
      { id: "chatgpt", name: "ChatGPT 订阅" },
    ]);
  });

  it("订阅来源接口不可用时不当作错误", async () => {
    api.listAuthProviders.mockRejectedValue(new Error("404"));

    await useModelsStore.getState().init();

    expect(useModelsStore.getState().authProviders).toEqual([]);
    expect(useModelsStore.getState().error).toBeNull();
  });

  it("重复初始化不重复请求", async () => {
    await useModelsStore.getState().init();
    await useModelsStore.getState().init();

    expect(api.readConfig).toHaveBeenCalledTimes(1);
  });
});

describe("写操作", () => {
  it("新增成功后一次应用列表与选择", async () => {
    api.createConnection.mockResolvedValue(CONNECTED);

    const ok = await useModelsStore.getState().create({
      display_name: "我的 DeepSeek",
      catalog_id: "deepseek",
      base_url: "https://api.deepseek.com/v1",
      api: "openai-completions",
      models: ["deepseek-chat"],
      default_model: "deepseek-chat",
      api_key: "sk-test",
    });

    expect(ok).toBe(true);
    expect(api.createConnection).toHaveBeenCalledWith(expect.any(Object), expect.any(String));
    expect(useModelsStore.getState().revision).toBe("r2");
    expect(useModelsStore.getState().selection.connection_id).toBe("conn_1");
    expect(useModelsStore.getState().connections).toHaveLength(1);
  });

  it("冲突只置位 conflict，不清空已读到的连接", async () => {
    useModelsStore.setState({ revision: "r1", connections: CONNECTED.connections });
    api.updateConnection.mockRejectedValue(
      new ModelApiError("MODEL_CONFIG_CONFLICT", "配置被改过", "重新读取", 409),
    );

    const ok = await useModelsStore
      .getState()
      .update("conn_1", {
        display_name: "x",
        catalog_id: "deepseek",
        base_url: "https://api.deepseek.com/v1",
        api: "openai-completions",
        models: ["deepseek-chat"],
        default_model: "deepseek-chat",
      });

    expect(ok).toBe(false);
    expect(useModelsStore.getState().conflict).toBe(true);
    expect(useModelsStore.getState().connections).toHaveLength(1);
    expect(useModelsStore.getState().error).toContain("重新读取");
  });

  it("删除当前连接后列表与选择一起更新", async () => {
    useModelsStore.setState({
      revision: "r2",
      connections: CONNECTED.connections,
      selection: CONNECTED.selection,
    });
    api.deleteConnection.mockResolvedValue(EMPTY);

    await useModelsStore.getState().remove("conn_1");

    expect(useModelsStore.getState().connections).toEqual([]);
    expect(useModelsStore.getState().selection.source).toBe("none");
  });

  it("切换模型以后端返回为准", async () => {
    useModelsStore.setState({ revision: "r1" });
    api.selectModel.mockResolvedValue({
      ...CONNECTED,
      selection: { ...CONNECTED.selection, model: "deepseek-reasoner" },
    });

    await useModelsStore.getState().choose("conn_1", "deepseek-reasoner");

    expect(api.selectModel).toHaveBeenCalledWith("conn_1", "deepseek-reasoner", "r1", undefined);
    expect(useModelsStore.getState().selection.model).toBe("deepseek-reasoner");
  });

  it("修改思考深度携带版本，并应用后端确认的值", async () => {
    useModelsStore.setState({ revision: "r2", selection: CONNECTED.selection });
    api.selectModel.mockResolvedValue({
      ...CONNECTED,
      selection: { ...CONNECTED.selection, thinking_level: "high", thinking_levels: ["low", "high"] },
    });

    await useModelsStore.getState().choose("conn_1", "deepseek-chat", "high");

    expect(api.selectModel).toHaveBeenCalledWith("conn_1", "deepseek-chat", "r2", "high");
    expect(useModelsStore.getState().selection.thinking_level).toBe("high");
  });

  it("思考深度保存失败时保留原选择并展示错误", async () => {
    useModelsStore.setState({ revision: "r2", selection: CONNECTED.selection });
    api.selectModel.mockRejectedValue(new ModelApiError(
      "MODEL_CONFIG_CONFLICT", "配置已更新", "重新读取后再提交", 409,
    ));

    expect(await useModelsStore.getState().choose("conn_1", "deepseek-chat", "high")).toBe(false);
    expect(useModelsStore.getState().selection).toEqual(CONNECTED.selection);
    expect(useModelsStore.getState().conflict).toBe(true);
  });
});

describe("全局状态不含密钥", () => {
  it("写操作参数里的 key 不会留在状态里", async () => {
    api.createConnection.mockResolvedValue(CONNECTED);

    await useModelsStore.getState().create({
      display_name: "x",
      catalog_id: "deepseek",
      base_url: "https://api.deepseek.com/v1",
      api: "openai-completions",
      models: ["deepseek-chat"],
      default_model: "deepseek-chat",
      api_key: "sk-secret-value",
    });

    expect(JSON.stringify(useModelsStore.getState())).not.toContain("sk-secret-value");
  });
});

describe("展示文本", () => {
  it("有选择时显示连接与模型", () => {
    expect(selectionLabel(CONNECTED.selection, CONNECTED.connections)).toBe(
      "我的 DeepSeek / deepseek-chat",
    );
  });

  it("未选择时给出下一步提示", () => {
    expect(selectionLabel(EMPTY.selection, [])).toBe("未连接模型");
  });
});
