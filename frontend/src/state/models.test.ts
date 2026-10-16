import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchModelSelection, selectModel } from "../api/models";
import { useModelStore } from "./models";
vi.mock("../api/models", () => ({
  fetchModelSelection: vi.fn(),
  selectModel: vi.fn(),
}));
const current = {
  provider: "deepseek",
  model: "deepseek-flash",
  display_name: "DeepSeek",
  available: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  useModelStore.setState({ current, error: "" });
});
describe("当前模型由服务端确认", () => {
  it("晚到的旧读取不能覆盖已经成功的新选择", async () => {
    let finish!: (value: typeof current) => void;
    vi.mocked(fetchModelSelection).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const refreshing = useModelStore.getState().refresh();
    const local = {
      provider: "ollama",
      model: "qwen3:4b",
      display_name: "Ollama",
      available: true,
    };
    vi.mocked(selectModel).mockResolvedValueOnce(local);
    await useModelStore.getState().select("ollama", "qwen3:4b");
    finish(current);
    await refreshing;
    expect(useModelStore.getState().current).toEqual(local);
  });
  it("切换失败保留当前模型", async () => {
    vi.mocked(selectModel).mockRejectedValueOnce(new Error("缺少密钥"));
    await expect(useModelStore.getState().select("other", "x")).rejects.toThrow(
      "缺少密钥",
    );
    expect(useModelStore.getState().current).toEqual(current);
  });
  it("成功后使用服务端返回的模型身份", async () => {
    const local = {
      provider: "ollama",
      model: "qwen3:4b",
      display_name: "Ollama",
      available: true,
    };
    vi.mocked(selectModel).mockResolvedValueOnce(local);
    await useModelStore.getState().select("ollama", "qwen3:4b");
    expect(useModelStore.getState().current).toEqual(local);
  });
  it("读取失败保留已知选择并给出错误", async () => {
    vi.mocked(fetchModelSelection).mockRejectedValueOnce(
      new Error("服务不可达"),
    );
    await useModelStore.getState().refresh();
    expect(useModelStore.getState().current).toEqual(current);
    expect(useModelStore.getState().error).toBe("服务不可达");
  });
});
