import type { ModuleSpec } from "../../api/moduleLibrary";
import { parseFields, fieldProblem, type EntryRow } from "../../state/moduleEditor";
import { Field } from "../ui/Field";
import { Button } from "../ui/Button";
import { FIELD_LABEL } from "./fieldLabels";

export default function EntryFields({ row, preset, onChange }: { row: EntryRow; preset: ModuleSpec | undefined; onChange: (patch: Partial<EntryRow>) => void }) {
  const fields = parseFields(row.fieldsText);
  const problem = fieldProblem(row, preset);
  function update(key: string, value: unknown) {
    const old = fields?.[key];
    const type = row.fieldTypes?.[key] ?? (typeof old === "number" ? "number" : typeof old === "boolean" ? "boolean" : "string");
    if (fields) onChange({ fieldsText: JSON.stringify({ ...fields, [key]: value }, null, 2), fieldTypes: { ...row.fieldTypes, [key]: type } });
  }
  return <div className="entry-fields">
    {fields && preset?.allowed_fields && <div className="entry-field-grid">{preset.allowed_fields.map(key => {
      const value = fields[key];
      if (Array.isArray(value) || (value !== undefined && typeof value === "object")) return <p key={key} className="ui-field-hint">{FIELD_LABEL[key] ?? key}：在高级字段中编辑。</p>;
      const type = row.fieldTypes?.[key] ?? (typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "string");
      return <div className="entry-field-control" key={key}>
        <Field label={FIELD_LABEL[key] ?? key}>{type === "boolean" ? <input type="checkbox" checked={value === true} onChange={event => update(key, event.target.checked)} />
          : <input value={value === undefined ? "" : String(value)} inputMode={type === "number" ? "decimal" : "text"} onChange={event => update(key, event.target.value)} />}</Field>
        <select className="ui-input" aria-label={`${FIELD_LABEL[key] ?? key}类型`} value={type} onChange={event => {
          const next = event.target.value as "string" | "number" | "boolean";
          onChange({ fieldTypes: { ...row.fieldTypes, [key]: next }, fieldsText: JSON.stringify({ ...fields, [key]: next === "boolean" ? false : value === undefined ? "" : String(value) }, null, 2) });
        }}><option value="string">文字</option><option value="number">数字</option><option value="boolean">是或否</option></select>
        {key in fields && <Button size="compact" variant="quiet" aria-label={`清除${FIELD_LABEL[key] ?? key}`} onClick={() => {
          const next = { ...fields }; delete next[key]; onChange({ fieldsText: JSON.stringify(next, null, 2) });
        }}>清除</Button>}
      </div>;
    })}</div>}
    <details open={problem ? true : undefined} className="advanced-fields"><summary>高级字段</summary>
      <Field label="条目字段" error={problem ?? undefined}><textarea className="font-mono" aria-label="条目字段" rows={6} value={row.fieldsText}
        onChange={event => onChange({ fieldsText: event.target.value, fieldTypes: undefined })} /></Field>
    </details>
  </div>;
}
