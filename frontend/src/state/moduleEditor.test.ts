import { beforeEach, expect, it } from "vitest";
import { useModuleEditorStore, moduleDraftKey, draftDirty, draftEntries, fieldProblem } from "./moduleEditor";
import type { RevisionDetail } from "../api/moduleLibrary";
const detail: RevisionDetail = { library_id: "lib", module_id: "A", revision_id: "r1", kind: "reference", name: "雨", entries: [{ entry_id: "rain", kind: "note", title: "雨", fields: {}, aliases: [], chars: 2, digest: "d" }] };
beforeEach(() => useModuleEditorStore.setState(useModuleEditorStore.getInitialState(), true));
it("模块、版本与库分别保存草稿，重读不覆盖无效JSON输入", () => {
  const store = useModuleEditorStore.getState();
  store.ensure(detail, { rain: "原文" });
  const key = moduleDraftKey(detail);
  store.edit(key, rows => rows.map(row => ({ ...row, body: "未保存", fieldsText: "{损坏" })));
  store.ensure({ ...detail, module_id: "B" }, { rain: "B原文" });
  store.ensure({ ...detail, revision_id: "r2" }, { rain: "第二版" });
  store.ensure({ ...detail, library_id: "other" }, { rain: "其他库" });
  store.ensure(detail, { rain: "再读取" });
  expect(useModuleEditorStore.getState().drafts[key].rows[0]).toMatchObject({ body: "未保存", fieldsText: "{损坏" });
  expect(Object.keys(useModuleEditorStore.getState().drafts)).toHaveLength(4);
  store.discard(key);
  expect(draftDirty(useModuleEditorStore.getState().drafts[key])).toBe(false);
  expect(useModuleEditorStore.getState().drafts[key].rows[0].body).toBe("原文");
});
it("发布期间的新编辑保留，并以提交快照作为新基线", () => {
  const store = useModuleEditorStore.getState();
  store.ensure(detail, { rain: "原文" });
  const key = moduleDraftKey(detail);
  store.edit(key, rows => rows.map(row => ({ ...row, body: "提交内容" })));
  const submitted = useModuleEditorStore.getState().drafts[key];
  store.edit(key, rows => rows.map(row => ({ ...row, body: "后来输入" })));
  const next = store.published(key, submitted.sequence, "r2", submitted.rows);
  expect(useModuleEditorStore.getState().drafts[next].rows[0].body).toBe("后来输入");
  expect(useModuleEditorStore.getState().drafts[next].baseline[0].body).toBe("提交内容");
  expect(draftDirty(useModuleEditorStore.getState().drafts[next])).toBe(true);
});

it("表单数字0、布尔false、空数字与高级标量列表沿用服务端语义", () => {
  const row = { entry_id: "e", kind: "author_event", title: "安排", body: "正文", aliasesText: "", fieldsText: '{"planned_chapter":"0","decision":false,"supports":["a",2]}', fieldTypes: { planned_chapter: "number" as const, decision: "boolean" as const } };
  const preset = { kind: "clues" as const, label: "线索", source_kinds: ["clue_plan"], organize_domain: "clues", allowed_fields: ["planned_chapter", "decision", "supports"] };
  expect(fieldProblem(row, preset)).toBeNull();
  expect(draftEntries([row])[0].fields).toEqual({ planned_chapter: 0, decision: false, supports: ["a", 2] });
  expect(draftEntries([{ ...row, fieldsText: '{"planned_chapter":""}' }])[0].fields).toEqual({});
});
it.each(['{"unknown":"x"}', '{"source":{"nested":true}}', '{"source":[null]}', '{损坏'])('不支持的字段输入保留用于修正，不能转换为空对象后发布：%s', text => {
  const row = { entry_id: "e", kind: "reference", title: "资料", body: "正文", aliasesText: "", fieldsText: text };
  const preset = { kind: "reference" as const, label: "资料", source_kinds: ["reference"], organize_domain: "glossary", allowed_fields: ["source"] };
  expect(fieldProblem(row, preset)).not.toBeNull();
  expect(row.fieldsText).toBe(text);
  if (text === '{损坏') expect(() => draftEntries([row])).toThrow("ENTRY_FIELDS_INVALID");
});
