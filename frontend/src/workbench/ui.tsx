import type { ComponentProps, ReactNode } from "react";
import { PAGE_SIZE, type TaskKey, url } from "./api";

export function SearchLink({ to, onClick, ...props }: ComponentProps<"a"> & { to: object }) {
  const href = url(window.location.pathname, to);
  return <a {...props} href={href} onClick={event => {
    onClick?.(event);
    if (event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  }} />;
}

export function navigate(to: object, replace = false) {
  const href = url(window.location.pathname, to);
  if (href === window.location.pathname + window.location.search) return;
  if (replace) window.history.replaceState(null, "", href);
  else window.history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function taskKey(params: URLSearchParams): TaskKey | null {
  const library_id = params.get("library_id");
  const task_id = params.get("task_id");
  const revision = params.get("revision");
  if (!library_id || !task_id || !revision || !/^[1-9][0-9]*$/.test(revision) || !Number.isSafeInteger(Number(revision))) return null;
  return { library_id, task_id, revision: Number(revision) };
}

export function Panel({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return <section className={`wb-panel ${className}`} aria-label={title}><h2>{title}</h2><div className="wb-body">{children}</div></section>;
}

export function Data({ value }: { value: unknown }) {
  if (value == null) return <p className="muted">未记录</p>;
  if (value === "" || (typeof value === "object" && !Object.keys(value).length)) return <p className="muted">已记录为空</p>;
  return <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

export function Notice({ error, loading }: { error?: string; loading?: boolean }) {
  return error ? <p className="wb-error" role="alert">{error}</p> : loading ? <p role="status" className="muted">正在读取…</p> : null;
}

const labels: Record<string, string> = {
  not_started: "未启动", running: "执行中", completed: "已完成", failed: "执行失败",
  unknown: "未知", pending: "待人工复核", reviewed: "已复核", not_applicable: "不适用",
  partial: "部分复核", blocked: "缺少证据", pass: "通过", fail: "失败", inconclusive: "待定",
  invalid: "运行无效", not_run: "未执行",
};

export function stateLabel(value?: string | null) {
  return value ? labels[value] ?? value : "未记录";
}

export function Verdict({ value }: { value?: string | null }) {
  return <span className="badge" data-verdict={value ?? undefined}>{stateLabel(value)}</span>;
}

export function displayDate(at?: string | null) {
  return at && Number.isFinite(Date.parse(at)) ? new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset",
  }).format(new Date(at)) : "时间未记录";
}

export function pageOffset(value: string | null) {
  const offset = Number(value);
  return Number.isSafeInteger(offset) && offset > 0 ? offset : 0;
}

export function Pagination({ offset, more, change }: { offset: number; more: boolean; change: (n: number) => void }) {
  if (!offset && !more) return null;
  return <div className="wb-actions" aria-label="分页">
    <button disabled={!offset} onClick={() => change(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
    <span className="muted">第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span>
    <button disabled={!more} onClick={() => change(offset + PAGE_SIZE)}>下一页</button>
  </div>;
}
