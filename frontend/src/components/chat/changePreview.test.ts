import { describe, expect, it } from "vitest";
import type { ModuleChangeSetView } from "../../api/moduleLibrary";
import { findChangePreview } from "./changePreview";

function view(overrides: Partial<ModuleChangeSetView> = {}): ModuleChangeSetView {
  return {
    change_set_id: "chs_1",
    status: "prepared",
    base_assembly_digest: null,
    turn_id: "t1",
    operations: [],
    saved: [],
    result: null,
    ...overrides,
  };
}

describe("变更集内容预览", () => {
  it("新建操作读取正文，名称优先取模型给的名字", () => {
    const detail = findChangePreview(
      view({
        operations: [
          {
            operation_id: "cast",
            action: "create",
            name: "人物小传",
            body: "# 核心嫌疑人\n\n七名嫌疑人。",
            chars: 18,
          },
        ],
      }),
      "cast",
    );
    expect(detail).toMatchObject({
      operationId: "cast",
      kind: "create",
      title: "人物小传",
      body: "# 核心嫌疑人\n\n七名嫌疑人。",
      edits: [],
    });
  });

  it("修改操作逐条给出原文、替换内容与整份重写正文", () => {
    const detail = findChangePreview(
      view({
        operations: [
          {
            operation_id: "cast",
            action: "update",
            module_id: "mod_1",
            binding_id: "binding_1",
            edits: [
              { entry_id: "doctor", base_digest: "sha", find: "医生", replace: "姐姐" },
              { entry_id: "butler", base_digest: "sha", body: "管家全文。" },
            ],
          },
        ],
        saved: [
          {
            operation_id: "cast",
            module_id: "mod_1",
            revision_id: "rev_2",
            name: "人物设定",
          },
        ],
      }),
      "cast",
    );
    expect(detail).toMatchObject({ kind: "update", title: "人物设定", body: "" });
    expect(detail?.edits).toEqual([
      { entryId: "doctor", find: "医生", replace: "姐姐", body: "" },
      { entryId: "butler", find: "", replace: "", body: "管家全文。" },
    ]);
  });

  it("没有名称时退回操作标识；找不到操作返回 null", () => {
    const data = view({
      operations: [{ operation_id: "premise", action: "create", body: "暴雪封山。" }],
    });
    expect(findChangePreview(data, "premise")?.title).toBe("premise");
    expect(findChangePreview(data, "missing")).toBeNull();
  });
});
