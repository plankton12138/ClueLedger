/**
 * 工作区菜单模型（P7 T1，计划 D2）：顶栏「案卷」下拉的条目构成纯函数。
 * 组件（WorkspaceMenu）只做开合调度与呈现；规则在此可测——
 * 工作区按原序展开（当前 slug 高亮；slug 失效时仍置顶保留以便感知），
 * 分隔线后跟「新建案卷」，选定工作区时再跟「导出备份」。
 */

export type WorkspaceMenuItem =
  | { kind: "workspace"; slug: string; current: boolean }
  | { kind: "separator" }
  | { kind: "create" }
  | { kind: "export"; slug: string };

/** 由（当前 slug, 工作区列表）派生菜单条目序列——纯函数，零 DOM。 */
export function buildWorkspaceMenu(
  slug: string | null,
  workspaces: string[],
): WorkspaceMenuItem[] {
  const items: WorkspaceMenuItem[] = [];
  // 状态里的 slug 不在列表（地址失效等）：置顶保留，让用户看到当前指向再决策
  if (slug !== null && !workspaces.includes(slug)) {
    items.push({ kind: "workspace", slug, current: true });
  }
  for (const w of workspaces) {
    items.push({ kind: "workspace", slug: w, current: w === slug });
  }
  items.push({ kind: "separator" });
  items.push({ kind: "create" });
  if (slug !== null) {
    items.push({ kind: "export", slug });
  }
  return items;
}
