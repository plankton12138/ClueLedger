import type { ModuleSpec } from "../../api/moduleLibrary";
import type { EntryRow } from "../../state/moduleEditor";
import EntryFields from "./EntryFields";
import { Field } from "../ui/Field";
import { Button } from "../ui/Button";

export default function ModuleEditor({ rows, preset, onChange }: { rows: EntryRow[]; preset: ModuleSpec | undefined; onChange: (update: (rows: EntryRow[]) => EntryRow[]) => void }) {
  const patch = (id: string, value: Partial<EntryRow>) => onChange(current => current.map(row => row.entry_id === id ? { ...row, ...value } : row));
  return <div className="module-editor">{rows.map(row => <article key={row.entry_id} className="module-entry-editor">
    <div className="entry-title"><Field label="条目标题"><input aria-label="条目标题" value={row.title} onChange={event => patch(row.entry_id, { title: event.target.value })} /></Field>
      <Button size="compact" variant="danger" onClick={() => onChange(current => current.filter(item => item.entry_id !== row.entry_id))}>移除条目</Button></div>
    <Field label="条目正文"><textarea aria-label="条目正文" rows={8} value={row.body} onChange={event => patch(row.entry_id, { body: event.target.value })} /></Field>
    <Field label="条目别名"><input aria-label="条目别名" value={row.aliasesText} onChange={event => patch(row.entry_id, { aliasesText: event.target.value })} /></Field>
    <EntryFields row={row} preset={preset} onChange={value => patch(row.entry_id, value)} />
  </article>)}
    <Button variant="quiet" onClick={() => onChange(current => [...current, { entry_id: `entry_${crypto.randomUUID().replace(/-/g, "")}`, kind: preset?.source_kinds[0] ?? "", title: "", body: "", aliasesText: "", fieldsText: "{}" }])}>添加条目</Button>
  </div>;
}
