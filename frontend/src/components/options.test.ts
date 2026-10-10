/**
 * parseOptions 纯函数剧本（P9 T4，D3）：合法块 / 无标记 / 格式残缺，
 * 外加 optionReply 文案。失败一律返回原文，不静默吞。
 */

import { describe, expect, it } from "vitest";
import { OPTIONS_MARKER, optionReply, parseOptions } from "./options";

describe("parseOptions 合法块", () => {
  it("标准两选项：拆出 body/key/label/note", () => {
    const text =
      "凶器可以走两种方向，你挑一个。\n\n【选项】\nA. 毒杀 —— 线索易布设，但易被猜中\nB. 钝器击打 —— 冲击力强，但要补凶器来源\n";
    const parsed = parseOptions(text);

    expect(parsed.options).not.toBeNull();
    expect(parsed.body).toBe("凶器可以走两种方向，你挑一个。");
    expect(parsed.options).toEqual([
      { key: "A", label: "毒杀", note: "线索易布设，但易被猜中" },
      { key: "B", label: "钝器击打", note: "冲击力强，但要补凶器来源" },
    ]);
  });

  it("三选项 + 键后标点/分隔符轻微漂移也能解析", () => {
    const text =
      "正文。\n\n【选项】\nA、方案甲 — 说明甲\nB. 方案乙 — 说明乙\nC．方案丙 -- 说明丙\n";
    const parsed = parseOptions(text);

    expect(parsed.options).toHaveLength(3);
    expect(parsed.options?.map((o) => o.key)).toEqual(["A", "B", "C"]);
    expect(parsed.options?.[0].label).toBe("方案甲");
    expect(parsed.options?.[2].note).toBe("说明丙");
  });

  it("选项无取舍说明时 note 为空串，仍算合法", () => {
    const parsed = parseOptions("正文\n\n【选项】\nA. 方案甲\nB. 方案乙\n");

    expect(parsed.options?.[0].note).toBe("");
    expect(parsed.options).toHaveLength(2);
  });
});

describe("parseOptions 无标记", () => {
  it("不含标记 → options=null 且 body 为原文", () => {
    const text = "这是一条普通回复，没有任何选项。";
    const parsed = parseOptions(text);

    expect(parsed.options).toBeNull();
    expect(parsed.body).toBe(text);
  });

  it("空串 → null", () => {
    expect(parseOptions("").options).toBeNull();
  });
});

describe("parseOptions 格式残缺", () => {
  it("标记后只有一个选项 → 原文", () => {
    const text = "正文\n\n【选项】\nA. 独一个 —— 说明\n";
    const parsed = parseOptions(text);

    expect(parsed.options).toBeNull();
    expect(parsed.body).toBe(text);
  });

  it("选项行无法解析（无键）→ 原文", () => {
    const text = "正文\n\n【选项】\n方案甲 —— 说明\n方案乙 —— 说明\n";
    expect(parseOptions(text).options).toBeNull();
  });

  it("键不连续（A、C）→ 原文", () => {
    const text = "正文\n\n【选项】\nA. 方案甲 —— 说明\nC. 方案丙 —— 说明\n";
    expect(parseOptions(text).options).toBeNull();
  });

  it("键不从 A 起（B、C）→ 原文", () => {
    const text = "正文\n\n【选项】\nB. 方案乙 —— 说明\nC. 方案丙 —— 说明\n";
    expect(parseOptions(text).options).toBeNull();
  });

  it("标记后无内容 → 原文", () => {
    const text = "正文里提到了【选项】两个字但没有块。";
    const parsed = parseOptions(text);

    expect(parsed.options).toBeNull();
    expect(parsed.body).toBe(text);
  });

  it("块后混入普通文本行 → 原文（块必须在最末尾）", () => {
    const text =
      "正文\n\n【选项】\nA. 方案甲 —— 说明\nB. 方案乙 —— 说明\n\n补充一句无关的话。\n";
    expect(parseOptions(text).options).toBeNull();
  });
});

describe("optionReply", () => {
  it("生成「我选 X：方案名」", () => {
    expect(optionReply({ key: "B", label: "钝器击打", note: "" })).toBe(
      "我选 B：钝器击打",
    );
  });
});

describe("OPTIONS_MARKER", () => {
  it("与协议锚点一致", () => {
    expect(OPTIONS_MARKER).toBe("【选项】");
  });
});
