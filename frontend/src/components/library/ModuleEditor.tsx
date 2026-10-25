import type { ModuleSpec } from "../../api/moduleLibrary";
import type { EntryRow } from "../../state/moduleEditor";
import EntryFields from "./EntryFields";
import { Field } from "../ui/Field";

export default function ModuleEditor({ rows, preset, onChange }: { rows: EntryRow[]; preset: ModuleSpec | undefined; onChange: (update: (rows: EntryRow[]) => EntryRow[]) => void }) {
  const patch = (id: string, value: Partial<EntryRow>) => onChange(current => current.map(row => row.entry_id === id ? { ...row, ...value } : row));
  return <div className="module-editor">{rows.map(row => <article key={row.entry_id} className="module-entry-editor">
    {rows.length>1&&<h3>{row.title}</h3>}
    <textarea aria-label="模块正文" rows={14} value={row.body} onChange={event => patch(row.entry_id, { body: event.target.value })} />
    {(row.aliasesText || row.fieldsText!=="{}")&&<details className="module-original-data"><summary>附加资料</summary>
      <Field label="别名"><input aria-label="条目别名" value={row.aliasesText} onChange={event => patch(row.entry_id, { aliasesText: event.target.value })} /></Field>
      <EntryFields row={row} preset={preset} onChange={value => patch(row.entry_id, value)} />
    </details>}
  </article>)}
  </div>;
}
