import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { RevisionDetail } from "../../api/moduleLibrary";
import { FIELD_LABEL } from "./fieldLabels";
export default function ModulePreview({ detail, bodies }: { detail: RevisionDetail; bodies: Record<string, string> }) {
  return <div className="module-preview">{detail.entries.map(entry => <article key={entry.entry_id}>
    <h3>{entry.title}</h3>
    {entry.aliases.length > 0 && <p className="ui-field-hint">{entry.aliases.join("、")}</p>}
    {Object.keys(entry.fields).length > 0 && <dl>{Object.entries(entry.fields).map(([key, value]) => <div key={key}><dt>{FIELD_LABEL[key] ?? key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "")}</dd></div>)}</dl>}
    <div className="prose-paper"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{bodies[entry.entry_id] ?? ""}</ReactMarkdown></div>
  </article>)}</div>;
}
