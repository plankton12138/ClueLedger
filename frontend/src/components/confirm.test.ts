/**
 * parseConfirm 纯函数剧本（P10 T10，D9）：确认块 / 无标记 / 格式异常，
 * 外加定式指令文案。失败一律返回原文，不静默吞。
 */

import { describe, expect, it } from "vitest";
import {
  CONFIRM_MARKER,
  CONFIRM_REPLY,
  DECLINE_REPLY,
  parseConfirm,
} from "./confirm";

describe("parseConfirm 合法块", () => {
  it("标准确认块：拆出 body/pending/after/decline", () => {
    const text =
      "Bible 已建完，请你过目。\n\n【确认】\n待确认：是否按当前账本推进到写作阶段\n确认后：会推进到 writing 阶段，之后可以开写第一章\n暂不：我会留在 bible_drafting，等你指出要补充的设定\n";
    const parsed = parseConfirm(text);

    expect(parsed.confirm).not.toBeNull();
    expect(parsed.malformed).toBe(false);
    expect(parsed.body).toBe("Bible 已建完，请你过目。");
    expect(parsed.confirm).toEqual({
      pending: "是否按当前账本推进到写作阶段",
      after: "会推进到 writing 阶段，之后可以开写第一章",
      decline: "我会留在 bible_drafting，等你指出要补充的设定",
    });
  });

  it("字段顺序漂移 / 全角冒号也能解析", () => {
    const text =
      "正文。\n\n【确认】\n确认后：推进到 writing\n暂不：留在原地\n待确认：要不要推进\n";
    const parsed = parseConfirm(text);

    expect(parsed.confirm?.pending).toBe("要不要推进");
    expect(parsed.confirm?.after).toBe("推进到 writing");
    expect(parsed.confirm?.decline).toBe("留在原地");
  });
});

describe("parseConfirm 无标记", () => {
  it("不含标记 → confirm=null 且 body 为原文", () => {
    const text = "这是一条普通回复，没有确认块。";
    const parsed = parseConfirm(text);

    expect(parsed.confirm).toBeNull();
    expect(parsed.malformed).toBe(false); // 无标记 ≠ 格式异常
    expect(parsed.body).toBe(text);
  });

  it("空串 → null", () => {
    expect(parseConfirm("").confirm).toBeNull();
  });
});

describe("parseConfirm 格式异常", () => {
  it("缺字段（只有待确认）→ malformed=true 且 body 为原文", () => {
    const text = "正文\n\n【确认】\n待确认：要不要推进\n";
    const parsed = parseConfirm(text);

    expect(parsed.confirm).toBeNull();
    expect(parsed.malformed).toBe(true);
    expect(parsed.body).toBe(text);
  });

  it("字段值为空 → malformed", () => {
    const text = "正文\n\n【确认】\n待确认：\n确认后：会推进\n暂不：留在原地\n";
    expect(parseConfirm(text).malformed).toBe(true);
  });

  it("块内混入无关行 → malformed", () => {
    const text = "正文\n\n【确认】\n待确认：要不要推进\n补充一句\n确认后：会推进\n暂不：留在原地\n";
    expect(parseConfirm(text).malformed).toBe(true);
  });

  it("字段重复 → malformed", () => {
    const text =
      "正文\n\n【确认】\n待确认：要不要推进\n确认后：会推进\n暂不：留在原地\n确认后：又推进\n";
    expect(parseConfirm(text).malformed).toBe(true);
  });
});

describe("确认块定式指令", () => {
  it("确认 / 暂不文案与 D9 约定一致", () => {
    expect(CONFIRM_REPLY).toBe("我确认，请继续推进");
    expect(DECLINE_REPLY).toBe("暂不确认，请说明还需补充什么");
  });
});

describe("CONFIRM_MARKER", () => {
  it("与协议锚点一致", () => {
    expect(CONFIRM_MARKER).toBe("【确认】");
  });
});