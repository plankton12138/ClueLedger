import { beforeEach, expect, it } from "vitest";
import { hasUnsavedChanges } from "./useUnsavedChanges";
import { newScope, useIdeasStore } from "../../state/ideas";
import { useModuleEditorStore } from "../../state/moduleEditor";
beforeEach(() => {
  useIdeasStore.setState(useIdeasStore.getInitialState(), true);
  useModuleEditorStore.setState(useModuleEditorStore.getInitialState(), true);
});
it("没有草稿时不拦截刷新；新建模块输入需要提醒，放弃后取消提醒", () => {
  expect(hasUnsavedChanges()).toBe(false);
  useModuleEditorStore.setState({ newDrafts: { temp: { kind: "reference", name: "", title: "", body: "未保存" } } });
  expect(hasUnsavedChanges()).toBe(true);
  useModuleEditorStore.setState({ newDrafts: {} });
  expect(hasUnsavedChanges()).toBe(false);
});
it("快速记录与条目修改稿都算未保存；清空或放弃后取消提醒", () => {
  useIdeasStore.setState({ workspaceId: "a", scopes: { a: newScope("a") } });
  const store = useIdeasStore.getState();
  store.setCaptureBody("第一份");
  expect(hasUnsavedChanges()).toBe(true);
  store.setCaptureBody("");
  expect(hasUnsavedChanges()).toBe(false);

  useIdeasStore.setState(state => ({
    scopes: {
      ...state.scopes,
      a: {
        ...state.scopes.a,
        editing: {
          idea_1: {
            key: "idea_1",
            workspaceId: "a",
            ideaId: "idea_1",
            body: "改了一半",
            editSequence: 1,
            base: { revisionId: "irev_1", digest: "d1", body: "原文" },
          },
        },
      },
    },
  }));
  expect(hasUnsavedChanges()).toBe(true);
  useIdeasStore.getState().discardEntry("idea_1");
  expect(hasUnsavedChanges()).toBe(false);
});
