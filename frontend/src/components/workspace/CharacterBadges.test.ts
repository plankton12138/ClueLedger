import { describe, expect, it } from "vitest";

import { ROLE_CLASS, ROLE_LABEL } from "./CharacterBadges";

/**
 * 标签颜色靠类名生效，而构建只保留源码里出现过的字面量类名。
 * 这里固定「每个身份都有完整类名」，避免新增身份时界面悄悄变成无颜色标签。
 */
describe("人物身份标签", () => {
  it("每个身份都有中文文字与完整类名", () => {
    for (const role of Object.keys(ROLE_LABEL) as (keyof typeof ROLE_LABEL)[]) {
      expect(ROLE_LABEL[role]).toBeTruthy();
      expect(ROLE_CLASS[role]).toBe(`role-badge role-${role}`);
    }
    expect(Object.keys(ROLE_CLASS).sort()).toEqual(Object.keys(ROLE_LABEL).sort());
  });
});
