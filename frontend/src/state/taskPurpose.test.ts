import { describe, expect, it } from "vitest";

import { looksLikeProseRequest } from "./taskPurpose";

describe("looksLikeProseRequest", () => {
  it("keeps explicit rewrites as prose even when 构思 is a qualifier", () => {
    expect(looksLikeProseRequest("根据构思，重写前两章")).toBe(true);
    expect(looksLikeProseRequest("根据构思写第一章")).toBe(true);
    expect(looksLikeProseRequest("重写第一章的正文")).toBe(true);
  });

  it("recognizes closed-page file rewrites when the open file is prose", () => {
    expect(
      looksLikeProseRequest("已关闭构思页，现在直接重写这两个文件", "正文/0001 渠边的第一具尸体.md"),
    ).toBe(true);
    expect(looksLikeProseRequest("重写这两个文件", "设定集/角色小传/舒尔茨.md")).toBe(false);
  });

  it("does not turn structure notes or discussions into prose requests", () => {
    expect(looksLikeProseRequest("写一段线索说明")).toBe(false);
    expect(looksLikeProseRequest("把这一段线索说明写进构思")).toBe(false);
    expect(looksLikeProseRequest("写第一章的构思")).toBe(false);
    expect(looksLikeProseRequest("写第一章的结构")).toBe(false);
    expect(looksLikeProseRequest("把结构改一下")).toBe(false);
  });
});
