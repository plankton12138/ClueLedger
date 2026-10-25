import type { ReactNode } from "react";
export function StatusMessage({ tone = "info", children, action }: { tone?: "info" | "error" | "success" | "warning"; children: ReactNode; action?: ReactNode }) {
  return <div className="ui-status" data-tone={tone} role={tone === "error" ? "alert" : "status"}><div>{children}</div>{action}</div>;
}
export function EmptyState({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div className="ui-empty"><p>{children}</p>{action}</div>;
}
