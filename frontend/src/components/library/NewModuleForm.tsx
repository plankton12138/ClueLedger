import { useState } from "react";
import { requestKey, type ModuleKind } from "../../api/moduleLibrary";
import { useModuleEditorStore } from "../../state/moduleEditor";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { KIND_LABEL, KIND_OPTIONS } from "./labels";

export default function NewModuleForm({ onDone }: { onDone: () => void }) {
  const [id, setId] = useState(() => requestKey("new-module"));
  const editor = useModuleEditorStore();
  const store = useModuleLibraryStore();
  const form = editor.newDrafts[id] ?? { kind: "characters" as ModuleKind, name: "", title: "", body: "" };
  function setForm(patch: Partial<typeof form>) { useModuleEditorStore.setState(state => ({ newDrafts: { ...state.newDrafts, [id]: { ...form, ...patch } } })); }
  function clear() { setForm({ name: "", title: "", body: "" }); setId(requestKey("new-module")); }
  return <div className="new-module-form"><h2>新建模块</h2>
    <Field label="类型"><select value={form.kind} onChange={event => setForm({ kind: event.target.value as ModuleKind })}>{KIND_OPTIONS.map(kind => <option key={kind} value={kind}>{KIND_LABEL[kind]}</option>)}</select></Field>
    <Field label="模块名称"><input value={form.name} onChange={event => setForm({ name: event.target.value })} /></Field>
    <Field label="第一条的标题"><input value={form.title} onChange={event => setForm({ title: event.target.value })} /></Field>
    <Field label="正文"><textarea rows={10} value={form.body} onChange={event => setForm({ body: event.target.value })} /></Field>
    <div className="module-edit-actions"><Button variant="primary" busy={store.busy} disabled={!form.name.trim() || !form.body.trim()} onClick={() => {
      const submitted = JSON.stringify(form);
      void store.create({ ...form, draftId: id }).then(ok => {
        if (ok && JSON.stringify(useModuleEditorStore.getState().newDrafts[id]) === submitted) { clear(); onDone(); }
      });
    }}>新建并发布第一版</Button><Button disabled={store.busy} onClick={() => { clear(); onDone(); }}>放弃修改</Button></div>
  </div>;
}
