/**
 * 变更集详情投影（0.8）。
 *
 * 准备结果在应用前没有作品绑定，侧栏读不到；这里把 `GET module-change-sets` 的详情
 * 整理成可直接阅读的形状：新建展示正文，已有模块展示每条修改的原文与替换内容。
 */

import type {
  ChangeEditInput,
  ModuleChangeOperation,
  ModuleChangeSetView,
} from "../../api/moduleLibrary";

export interface ChangePreviewEdit {
  entryId: string;
  find: string;
  replace: string;
  /** 整份重写时的新正文；局部替换时为空。 */
  body: string;
}

export interface ChangePreview {
  operationId: string;
  kind: "create" | "update";
  title: string;
  /** 新建内容的正文；修改操作没有整份正文时为空。 */
  body: string;
  edits: ChangePreviewEdit[];
}

export function findChangePreview(
  view: ModuleChangeSetView,
  operationId: string,
): ChangePreview | null {
  const operation = view.operations.find((item) => item.operation_id === operationId);
  if (!operation) return null;
  const saved = view.saved.find((item) => item.operation_id === operationId);
  const title = operation.name || saved?.name || operationId;
  if (operation.action === "create") {
    return {
      operationId,
      kind: "create",
      title,
      body: typeof operation.body === "string" ? operation.body : "",
      edits: [],
    };
  }
  return {
    operationId,
    kind: "update",
    title,
    body: "",
    edits: editRows(operation),
  };
}

function editRows(operation: ModuleChangeOperation): ChangePreviewEdit[] {
  if (!Array.isArray(operation.edits)) return [];
  return (operation.edits as ChangeEditInput[]).map((edit) => ({
    entryId: edit.entry_id,
    find: edit.find ?? "",
    replace: edit.replace ?? "",
    body: edit.body ?? "",
  }));
}
