import { beforeEach, expect, it } from "vitest";
import { hasUnsavedChanges } from "./useUnsavedChanges";
import { useIdeasStore } from "../../state/ideas";
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
it("切到另一条灵感后，后台草稿仍需提醒，放弃当前只影响当前草稿", () => {
  useIdeasStore.setState({ workspaceId: "a" });
  const store = useIdeasStore.getState();
  store.startNew(); store.setDraft({ body: "第一份" });
  const first = useIdeasStore.getState().activeKey!;
  store.startNew(); store.setDraft({ body: "第二份" });
  store.cancel();
  expect(hasUnsavedChanges()).toBe(true);
  store.selectDraft(first); store.cancel();
  expect(hasUnsavedChanges()).toBe(false);
});
