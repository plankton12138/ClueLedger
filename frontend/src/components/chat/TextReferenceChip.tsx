import type { TextReference } from "../../state/textReferences";

export default function TextReferenceChip({ reference, onRemove }: { reference: TextReference; onRemove(): void }) {
  return <div className="text-reference-chip">
    <details>
      <summary title={reference.source}>{reference.title}：{reference.text.replace(/\s+/g, " ").slice(0, 60)}</summary>
      <blockquote>{reference.text}</blockquote>
    </details>
    <button type="button" aria-label={`移除选段：${reference.title}`} onClick={onRemove}>×</button>
  </div>;
}
