import { useCallback, useEffect, useState } from "react";

export type TaskKey = { library_id: string; task_id: string; revision: number };
export type TaskRow = TaskKey & { title: string };
export type Rule = Record<string, unknown>;
export type Grader = { checkpoint: { case: string; step: string }; role: string; evidence: string[]; rule: Rule };
export type Task = {
  schema_version: number;
  scenario: {
    id: string; revision: number; title: string; description: string; risks?: string[];
    environment: Rule; messages: Record<string, string>;
    cases: Record<string, { title: string; fixture: Rule; steps: { id: string; message: string }[] }>;
  };
  code_graders: Record<string, Grader>;
  llm_graders: Record<string, Grader>;
};
export type TaskDetail = { task: Task; revisions: number[]; runnable: boolean; reason?: string };
export type Page<T> = { items: T[]; has_more: boolean };
export type TaskPage = Page<TaskRow> & { libraries: string[] };
export type Run = {
  run_id: string; kind: "task" | "regression"; task?: TaskKey | null;
  title?: string | null; started_at?: string | null; execution_status: string;
  verdict?: string | null; model?: string | null; turn_id?: string | null;
  review_status?: string | null; baseline_id?: string | null;
};
export type RunPage = Page<Run> & { default_run_id?: string | null };
export type Check = {
  check_id: string; case_id?: string; step_id?: string; role?: string; verdict: string;
  reason: string; evidence_refs: string[]; trace_refs?: string[]; judge?: Rule;
};
export type Report = { verdict: string; checks: Check[] };
export type Milestone = { at?: string | null; type: string; summary: string; trace_refs?: string[] };
export type RunDetail = {
  run: Run; report: Report | null; review_status: string; evidence_sha256?: string;
  review_inputs: Record<string, { rule: Rule; evidence_refs: string[] }>;
  milestones: Milestone[]; baseline_id?: string | null;
  evidence_versions: string[]; evidence_version?: string;
  report_versions: string[]; report_version?: string; available_evidence: string[];
};
export type TraceArchive = { events: import("../components/trace/types").TraceEvent[]; details: Record<string, Rule> };
export const PAGE_SIZE = 20;

export function runUrl(id: string, suffix = "") {
  return `/api/runs/${encodeURIComponent(id)}${suffix}`;
}

export function url(path: string, params: object = {}) {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value != null && value !== "") query.set(name, String(value));
  }
  return query.size ? `${path}?${query}` : path;
}

export function taskUrl(key: TaskKey) {
  return `/api/tasks/${encodeURIComponent(key.library_id)}/${encodeURIComponent(key.task_id)}/${key.revision}`;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", mode: "same-origin", cache: "no-store", ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const reason = payload?.detail ?? payload?.reason ?? payload?.error;
    throw new Error(typeof reason === "string" ? reason : reason?.why ?? `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body: object) {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Workbench-Intent": "local" },
    body: JSON.stringify(body),
  });
}

export function useResource<T>(path: string | null, { keepDataOnError = false, refreshKey }: { keepDataOnError?: boolean; refreshKey?: string } = {}) {
  const [loaded, setLoaded] = useState<{ path: string; data?: T; error?: string; failures?: number }>();
  const [generation, refresh] = useState(0);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    request<T>(path, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setLoaded({ path, data }); },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setLoaded(previous => {
          const sameResource = previous?.path === path ? previous : undefined;
          return {
            path, error: message, data: keepDataOnError ? sameResource?.data : undefined,
            failures: (sameResource?.failures ?? 0) + 1,
          };
        });
      },
    );
    return () => controller.abort();
  }, [path, generation, keepDataOnError, refreshKey]);
  const reload = useCallback(() => refresh(g => g + 1), []);
  const accept = useCallback((data: T) => { if (path) setLoaded({ path, data }); }, [path]);
  return { ...(loaded?.path === path ? loaded : undefined), loading: Boolean(path && loaded?.path !== path), refresh: reload, accept };
}
