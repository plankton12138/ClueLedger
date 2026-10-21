/**
 * 连接默认名称（0.4 V04-9 补充）。
 *
 * 新增连接时名称先填好：默认供应商名，重名按序号往后排，作者仍可手改。
 */

import { describe, expect, it } from "vitest";

import { defaultConnectionName } from "./naming";

const DEEPSEEK = { display_name: "DeepSeek", custom: false };
const CUSTOM = { display_name: "自定义服务", custom: true };

describe("新增连接的默认名称", () => {
  it("没有同名连接时直接用供应商名", () => {
    expect(defaultConnectionName(DEEPSEEK, [])).toBe("DeepSeek");
  });

  it("已有同名连接时加序号，避免列表里两个一样的名字", () => {
    const existing = [{ display_name: "DeepSeek" }];

    expect(defaultConnectionName(DEEPSEEK, existing)).toBe("DeepSeek 2");
    expect(defaultConnectionName(DEEPSEEK, [...existing, { display_name: "DeepSeek 2" }])).toBe(
      "DeepSeek 3",
    );
  });

  it("自定义服务也预填，不留给作者空框", () => {
    expect(defaultConnectionName(CUSTOM, [])).toBe("自定义服务");
  });

  it("供应商名为空时给一个能辨认的兜底", () => {
    expect(defaultConnectionName({ display_name: "  ", custom: true }, [])).toBe("自定义服务");
    expect(defaultConnectionName({ display_name: "", custom: false }, [])).toBe("新连接");
  });
});
