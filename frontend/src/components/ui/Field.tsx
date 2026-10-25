import { useId, type ReactElement } from "react";
import { cloneElement } from "react";
export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: ReactElement }) {
  const id = useId(), description = `${id}-description`;
  return <div className="ui-field"><label htmlFor={id}>{label}</label>
    {cloneElement(children, { id, "aria-invalid": Boolean(error), "aria-describedby": error || hint ? description : undefined, className: `ui-input ${children.props.className ?? ""}` })}
    {(error || hint) && <p id={description} role={error ? "alert" : undefined} className={error ? "ui-field-error" : "ui-field-hint"}>{error || hint}</p>}
  </div>;
}
