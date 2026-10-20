/**
 * 主工作区壳状态（M01/V03-1.1）——主区一次只展开一个，阅读位置按章保存。
 *
 * 核心不变量：
 *
 * - 主区只选择「哪一章正文」（V03-10 M6 收窄后，`MainView` 只有 manuscript 一个
 *   变体，对话固定在右侧栏、不占主区）；
 * - 阅读位置按 `project_id + chapter_id + source_kind + edit_session_id`
 *   四元组保存，禁止全局单个 scrollTop（M01 第 3 条）。
 *
 * 纯函数可测（vitest node 环境）；React 壳层接线消费这些函数。
 */

/**
 * 主区可辨识联合：一次只展开一个（计划 M01 目标示例收编）。
 *
 * V03-10 M6：对话成为右侧固定栏后，主区只剩「哪一章正文」一个选择；
 * `mainView === null` 表示尚未确定章节（无正文时按构思状态处理）。
 */
export type MainView = { kind: "manuscript"; chapterId: string };

/** 阅读位置键四元组（M01 第 3 条）。 */
export type SourceKind = "formal" | "edit_draft" | "candidate";

export interface ReadingPosition {
  projectId: string;
  chapterId: string;
  sourceKind: SourceKind;
  /** 修改副本会话 ID；正式稿阅读为 null（I01：身份不同，键不同）。 */
  editSessionId: string | null;
  /** 章内滚动位置；讨论另有滚动键，不共用。 */
  scrollTop?: number;
}

export interface DeskState {
  projectId: string | null;
  mainView: MainView | null;
  /** 四元组键 → 阅读位置；按项目隔离。 */
  readingPositions: Record<string, ReadingPosition>;
}

export function emptyDeskState(projectId: string | null = null): DeskState {
  return { projectId, mainView: null, readingPositions: {} };
}

/** 阅读位置四元组键：source_kind/edit_session_id 不同即不同位置。 */
export function readingPositionKey(position: {
  projectId: string;
  chapterId: string;
  sourceKind: SourceKind;
  editSessionId: string | null;
}): string {
  return [
    position.projectId,
    position.chapterId,
    position.sourceKind,
    position.editSessionId ?? "-",
  ].join("|");
}

/** 作者显式导航：主区唯一变更入口。 */
export function navigate(state: DeskState, view: MainView | null): DeskState {
  return { ...state, mainView: view };
}

/** 保存/更新阅读位置；不改主区。 */
export function saveReadingPosition(
  state: DeskState,
  position: ReadingPosition,
): DeskState {
  const key = readingPositionKey(position);
  return {
    ...state,
    readingPositions: { ...state.readingPositions, [key]: position },
  };
}

/**
 * 壳层初始焦点（M01 第 1 条）：有正文恢复上次章节；无记录取目录第一章；
 * 没有正文返回 null（只有未采用候选仍按构思状态处理）。
 */
export function selectInitialChapterId(
  chapterIds: readonly string[],
  state: DeskState,
  projectId: string,
): string | null {
  if (chapterIds.length === 0) return null;
  const saved = Object.values(state.readingPositions).find(
    (p) => p.projectId === projectId && p.sourceKind === "formal",
  );
  if (saved && chapterIds.includes(saved.chapterId)) return saved.chapterId;
  return chapterIds[0];
}
