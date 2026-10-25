import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { showEntryTitle, type ModuleDocument } from "../../state/moduleContent";
import { FIELD_LABEL } from "./fieldLabels";

export default function ModuleContent({ document }: { document: ModuleDocument }) {
  return <div className="module-preview">{document.detail.entries.map(entry => {
    const heading: Components["h1"] = ({ node, children }) => <h3 id={`module-heading-${entry.entry_id}-${node?.position?.start.line}`}>{children}</h3>;
    return <article key={entry.entry_id}>
      {showEntryTitle(document, entry) && <h3 id={`module-entry-${entry.entry_id}`}>{entry.title}</h3>}
      <div className="prose-paper"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ h1: heading, h2: heading, h3: heading, h4: heading, h5: heading, h6: heading }}>{document.bodies[entry.entry_id] ?? ""}</ReactMarkdown></div>
      {(entry.aliases.length > 0 || Object.keys(entry.fields).length > 0) && <details className="module-original-data"><summary>附加资料</summary>
        {entry.aliases.length > 0 && <p>{entry.aliases.join("、")}</p>}
        <dl>{Object.entries(entry.fields).map(([key,value]) => <div key={key}><dt>{FIELD_LABEL[key] ?? key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "")}</dd></div>)}</dl>
      </details>}
    </article>;
  })}</div>;
}
