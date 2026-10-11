/**
 * parseOptions 纯函数剧本（P9 T4，D3；P10 T11，D10 多维度升级）：合法块 / 无标记 /
 * 格式残缺 / 多块分组，外加 optionReply / multiOptionReply 文案。失败一律返回原文。
 */

import { describe, expect, it } from "vitest";
import {
  OPTIONS_MARKER,
  multiOptionReply,
  optionReply,
  parseOptions,
} from "./options";

/** 单组场景的便捷取用：断言解析成功时取第一组选项。 */
function firstOptions(text: string) {
  return parseOptions(text).groups?.[0].options ?? null;
}

describe("parseOptions 合法块（单组）", () => {
  it("标准两选项：拆出 body/key/label/note，dimension=null", () => {
    const text =
      "凶器可以走两种方向，你挑一个。\n\n【选项】\nA. 毒杀 —— 线索易布设，但易被猜中\nB. 钝器击打 —— 冲击力强，但要补凶器来源\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).not.toBeNull();
    expect(parsed.malformed).toBe(false);
    expect(parsed.body).toBe("凶器可以走两种方向，你挑一个。");
    expect(parsed.groups).toHaveLength(1);
    expect(parsed.groups?.[0].dimension).toBeNull();
    expect(parsed.groups?.[0].options).toEqual([
      { key: "A", label: "毒杀", note: "线索易布设，但易被猜中" },
      { key: "B", label: "钝器击打", note: "冲击力强，但要补凶器来源" },
    ]);
  });

  it("三选项 + 键后标点/分隔符轻微漂移也能解析", () => {
    const text =
      "正文。\n\n【选项】\nA、方案甲 — 说明甲\nB. 方案乙 — 说明乙\nC．方案丙 -- 说明丙\n";
    const options = firstOptions(text);

    expect(options).toHaveLength(3);
    expect(options?.map((o) => o.key)).toEqual(["A", "B", "C"]);
    expect(options?.[0].label).toBe("方案甲");
    expect(options?.[2].note).toBe("说明丙");
  });

  it("选项无取舍说明时 note 为空串，仍算合法", () => {
    const options = firstOptions("正文\n\n【选项】\nA. 方案甲\nB. 方案乙\n");

    expect(options?.[0].note).toBe("");
    expect(options).toHaveLength(2);
  });
});

describe("parseOptions 无标记", () => {
  it("不含标记 → groups=null 且 body 为原文", () => {
    const text = "这是一条普通回复，没有任何选项。";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.malformed).toBe(false); // 无标记 ≠ 格式异常
    expect(parsed.body).toBe(text);
  });

  it("空串 → null", () => {
    expect(parseOptions("").groups).toBeNull();
  });
});

describe("parseOptions 格式残缺", () => {
  it("标记后只有一个选项 → 原文", () => {
    const text = "正文\n\n【选项】\nA. 独一个 —— 说明\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.body).toBe(text);
  });

  it("选项行无法解析（无键）→ 原文", () => {
    const text = "正文\n\n【选项】\n方案甲 —— 说明\n方案乙 —— 说明\n";
    expect(parseOptions(text).groups).toBeNull();
  });

  it("键不连续（A、C）→ 原文", () => {
    const text = "正文\n\n【选项】\nA. 方案甲 —— 说明\nC. 方案丙 —— 说明\n";
    expect(parseOptions(text).groups).toBeNull();
  });

  it("键不从 A 起（B、C）→ 原文", () => {
    const text = "正文\n\n【选项】\nB. 方案乙 —— 说明\nC. 方案丙 —— 说明\n";
    expect(parseOptions(text).groups).toBeNull();
  });

  it("标记后无内容 → 原文", () => {
    const text = "正文里提到了【选项】两个字但没有块。";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.body).toBe(text);
  });

  it("块后混入普通文本行 → 原文（块必须在最末尾）", () => {
    const text =
      "正文\n\n【选项】\nA. 方案甲 —— 说明\nB. 方案乙 —— 说明\n\n补充一句无关的话。\n";
    expect(parseOptions(text).groups).toBeNull();
  });
});

describe("parseOptions malformed 标记（P10 T6，D5）", () => {
  it("标记存在但解析失败 → malformed=true 且 body 为原文", () => {
    const text = "正文\n\n【选项】\n方案甲 —— 说明\n方案乙 —— 说明\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.malformed).toBe(true);
    expect(parsed.body).toBe(text); // 原样渲染，不吞内容
  });

  it("键不连续（A、C）→ malformed=true", () => {
    const text = "正文\n\n【选项】\nA. 方案甲 —— 说明\nC. 方案丙 —— 说明\n";
    expect(parseOptions(text).malformed).toBe(true);
  });

  it("L5 压测实录：### 【选项】+ 维度标题行穿插 → malformed=true", () => {
    // 8B 弱模型实际输出：标记带 ### 前缀、维度标题行（**章纲划分**）穿插在选项行间、
    // 全角冒号替代「——」。前端按设计整条降级为原文，malformed 供尾部提示。
    const text =
      "### 【选项】\n**章纲划分**\nA. **三幕式结构**：ch01→ch02→ch03\nB. **双线并行**：ch01→ch02→ch03→ch04\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.malformed).toBe(true);
    expect(parsed.body).toBe(text);
  });
});

describe("parseOptions 多维度块（P10 T11，D10）", () => {
  it("两块各自带维度名 → 两组、各块键各自从 A 起", () => {
    const text =
      "两个维度都要定。\n\n【选项：章纲划分】\nA. 三幕式 —— 结构稳\nB. 双线并行 —— 信息量大\n\n【选项：叙诡手法】\nA. 叙述性诡计 —— 冲击强\nB. 不可靠叙述 —— 需铺垫\n";
    const parsed = parseOptions(text);

    expect(parsed.malformed).toBe(false);
    expect(parsed.body).toBe("两个维度都要定。");
    expect(parsed.groups).toHaveLength(2);
    expect(parsed.groups?.[0].dimension).toBe("章纲划分");
    expect(parsed.groups?.[0].options.map((o) => o.key)).toEqual(["A", "B"]);
    expect(parsed.groups?.[1].dimension).toBe("叙诡手法");
    expect(parsed.groups?.[1].options.map((o) => o.key)).toEqual(["A", "B"]);
  });

  it("半角冒号维度名也能解析", () => {
    const text =
      "正文\n\n【选项:节奏】\nA. 快 —— 紧张\nB. 慢 —— 细腻\n";
    const parsed = parseOptions(text);

    expect(parsed.groups?.[0].dimension).toBe("节奏");
  });

  it("无维度名单块 → 单组 dimension=null，向后兼容", () => {
    const text = "正文\n\n【选项】\nA. 方案甲 —— 说明\nB. 方案乙 —— 说明\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toHaveLength(1);
    expect(parsed.groups?.[0].dimension).toBeNull();
  });

  it("任一块解析失败 → 整条 malformed，返回原文", () => {
    const text =
      "正文\n\n【选项：章纲划分】\nA. 三幕式 —— 结构稳\nB. 双线并行 —— 信息量大\n\n【选项：叙诡手法】\n方案甲 —— 说明\n方案乙 —— 说明\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.malformed).toBe(true);
    expect(parsed.body).toBe(text);
  });

  it("维度名块内穿插标题行 → malformed", () => {
    const text =
      "正文\n\n【选项：节奏】\n**节奏控制**\nA. 快 —— 紧张\nB. 慢 —— 细腻\n";
    const parsed = parseOptions(text);

    expect(parsed.groups).toBeNull();
    expect(parsed.malformed).toBe(true);
  });
});

describe("optionReply / multiOptionReply", () => {
  it("单组：生成「我选 X：方案名」", () => {
    expect(optionReply({ key: "B", label: "钝器击打", note: "" })).toBe(
      "我选 B：钝器击打",
    );
  });

  it("多组：生成合并回复，按维度名拼接", () => {
    const groups = [
      { dimension: "章纲划分", options: [{ key: "A", label: "三幕式", note: "" }, { key: "B", label: "双线", note: "" }] },
      { dimension: "叙诡手法", options: [{ key: "A", label: "叙述性诡计", note: "" }, { key: "B", label: "不可靠叙述", note: "" }] },
    ];
    const reply = multiOptionReply(groups, [
      { key: "B", label: "双线", note: "" },
      { key: "A", label: "叙述性诡计", note: "" },
    ]);

    expect(reply).toBe(
      "关于「章纲划分」我选 B：双线；关于「叙诡手法」我选 A：叙述性诡计",
    );
  });
});

describe("OPTIONS_MARKER", () => {
  it("与协议锚点一致", () => {
    expect(OPTIONS_MARKER).toBe("【选项】");
  });
});