/**
 * 工作区菜单条目构成剧本（P7 T1，计划 D2）：顺序 / 高亮 / 失效 slug 置顶 /
 * 分隔线与操作项的派生规则。组件开合属 DOM 行为，走浏览器人工验收。
 */

import { describe, expect, it } from "vitest";
import { buildWorkspaceMenu } from "./workspaceMenu";

describe("buildWorkspaceMenu", () => {
  it("多工作区：按原序展开，当前项 current=true，尾随分隔线+新建+导出", () => {
    const items = buildWorkspaceMenu("b", ["a", "b", "c"]);
    expect(items).toEqual([
      { kind: "workspace", slug: "a", current: false },
      { kind: "workspace", slug: "b", current: true },
      { kind: "workspace", slug: "c", current: false },
      { kind: "separator" },
      { kind: "create" },
      { kind: "export", slug: "b" },
    ]);
  });

  it("未选定 slug（null）：无导出项，工作区全部 current=false", () => {
    const items = buildWorkspaceMenu(null, ["a", "b"]);
    expect(items).toEqual([
      { kind: "workspace", slug: "a", current: false },
      { kind: "workspace", slug: "b", current: false },
      { kind: "separator" },
      { kind: "create" },
    ]);
  });

  it("slug 失效（不在列表）：置顶保留一个 current 项，导出仍指向该 slug", () => {
    const items = buildWorkspaceMenu("ghost", ["a"]);
    expect(items).toEqual([
      { kind: "workspace", slug: "ghost", current: true },
      { kind: "workspace", slug: "a", current: false },
      { kind: "separator" },
      { kind: "create" },
      { kind: "export", slug: "ghost" },
    ]);
  });

  it("空列表 + 未选定：只有分隔线与新建（引导态最简菜单）", () => {
    expect(buildWorkspaceMenu(null, [])).toEqual([
      { kind: "separator" },
      { kind: "create" },
    ]);
  });
});
