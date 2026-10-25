import type { ButtonHTMLAttributes, ReactNode } from "react";
export function ListItem({ selected, summary, children, className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean; summary?: ReactNode }) {
  return <button {...props} type="button" aria-current={selected ? "true" : undefined} className={`ui-list-item ${className}`}>
    <span className="ui-list-title">{children}</span>{summary && <span className="ui-list-summary">{summary}</span>}
  </button>;
}
