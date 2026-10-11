import { describe, expect, it } from "vitest";

import type { CatalogEntry } from "../api/rest";
import { emptyLoginForm, hostOf, prefillLoginForm, suggestProfileName } from "./providerLogin";

const entry: CatalogEntry = {
  name: "deepseek",
  display_name: "DeepSeek",
  base_url: "https://api.deepseek.com/v1",
  default_model: "deepseek-chat",
  models: ["deepseek-chat", "deepseek-reasoner"],
  api_key_env: "DEEPSEEK_API_KEY",
  key_url: "https://platform.deepseek.com/api_keys",
};

describe("suggestProfileName", () => {
  it("不重名时原样返回", () => {
    expect(suggestProfileName("deepseek", ["zhipu", "openai"])).toBe("deepseek");
  });

  it("重名时追加序号后缀，跳过已占用", () => {
    expect(suggestProfileName("deepseek", ["deepseek"])).toBe("deepseek-2");
    expect(suggestProfileName("deepseek", ["deepseek", "deepseek-2"])).toBe("deepseek-3");
  });
});

describe("prefillLoginForm", () => {
  it("目录条目预填 base_url/env/model，name 带重名建议，key 恒空", () => {
    const form = prefillLoginForm(entry, ["deepseek"]);

    expect(form).toEqual({
      name: "deepseek-2",
      base_url: "https://api.deepseek.com/v1",
      api_key: "",
      api_key_env: "DEEPSEEK_API_KEY",
      default_model: "deepseek-chat",
      no_auth: false,
    });
  });
});

describe("emptyLoginForm", () => {
  it("自定义路径：全空表单", () => {
    expect(emptyLoginForm()).toEqual({
      name: "",
      base_url: "",
      api_key: "",
      api_key_env: "",
      default_model: "",
      no_auth: false,
    });
  });
});

describe("hostOf", () => {
  it("取 base_url 域名，非法 URL 原样返回", () => {
    expect(hostOf("https://api.deepseek.com/v1")).toBe("api.deepseek.com");
    expect(hostOf("not-a-url")).toBe("not-a-url");
  });
});
