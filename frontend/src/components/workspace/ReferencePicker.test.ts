import { describe, expect, it } from "vitest";

import type { Catalog, FileEntry } from "../../api/workspace";
import { referenceGroups } from "./ReferencePicker";

function entry(path: string, title: string, exists: boolean): FileEntry {
  return {
    path,
    title,
    display_title: title,
    kind: "setting",
    order: "",
    chapter_number: null,
    chapter_type: null,
    roles: [],
    exists,
    size: exists ? 10 : 0,
    metadata_warnings: [],
  };
}

describe("referenceGroups", () => {
  it("只列出真实存在的文件，缺失的固定设定不再显示", () => {
    const catalog = {
      chapters: [entry("正文/0001.md", "第一章", true)],
      characters: [entry("设定集/角色小传/甲.md", "甲", false)],
      settings: [entry("设定/线索.md", "线索", false), entry("设定/真相.md", "真相", false)],
    } as unknown as Catalog;

    expect(referenceGroups(catalog)).toEqual([
      { label: "正文", entries: [expect.objectContaining({ path: "正文/0001.md" })] },
    ]);
  });

  it("存在固定设定时保留该组", () => {
    const catalog = {
      chapters: [],
      characters: [],
      settings: [entry("设定/真相.md", "真相", true)],
    } as unknown as Catalog;

    expect(referenceGroups(catalog)).toEqual([
      { label: "固定设定", entries: [expect.objectContaining({ path: "设定/真相.md" })] },
    ]);
  });
});
