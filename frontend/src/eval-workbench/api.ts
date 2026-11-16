import type { TurnTrace } from "../components/trace";

export type CheckRow = {
  id: string;
  level: string;
  verdict: string;
  reason: string;
  evidence?: EvidenceRef[];
};

export type EvidenceRef = {
  type: string;
  path?: string;
  anchor?: string;
  fragment?: string;
  turn_id?: string;
  span_id?: string;
  expected?: string;
  operation_id?: string;
  text?: string;
};

export type StageRow = {
  id: string;
  verdict: string;
  span_id?: string | null;
  turn_id?: string;
  reason?: string;
};

export type AttemptRow = {
  case_id: string;
  attempt: number;
  status: string;
  outcome?: string | null;
  model_requests?: number;
  error?: string;
  checks: CheckRow[];
  stages?: StageRow[];
  first_failed_stage?: string | null;
};

export type CaseView = {
  case_id: string;
  attempts: AttemptRow[];
  must_failed?: boolean;
  quality_pass_count?: number;
  attempt_count?: number;
};

export type Report = {
  mode?: string;
  identity?: Record<string, unknown>;
  summary?: Record<string, number>;
  cases?: CaseView[];
  attempts?: AttemptRow[];
  gate?: { passed?: boolean; blockers?: { kind: string; case_id: string; detail: string }[]; warnings?: { kind: string; case_id: string; detail: string }[] };
  baseline_compare?: {
    baseline_identity?: Record<string, unknown>;
    cases?: { case_id: string; quality_pass_count: number; baseline_quality_pass_count: number; delta: string }[];
  } | null;
  attributions?: Attribution[];
};

export type Review = {
  case_id: string;
  attempt: number;
  check_id: string;
  verdict: string;
  evidence_ref?: string;
  reason?: string;
};

export type Attribution = {
  case_id: string;
  attempt: number;
  category: string;
  note?: string;
};

export type AttemptPayload = {
  case_id: string;
  attempt: number;
  row: AttemptRow;
  meta: { turns?: { turn_id: string; author?: string; focus?: string }[]; conversation_id?: string };
  turns: { turn_id: string; has_trace: boolean }[];
  expect: {
    region?: { path: string; from: string; to: string };
    preserve?: string[];
    steps?: { author: string; focus?: string | null }[];
  } | null;
  has_evidence: boolean;
  stage_labels: Record<string, string>;
  default_attribution: string;
  attribution_options: { id: string; label: string; hint: string }[];
};

export type DiffView = {
  path: string;
  before: string;
  after: string;
  region: { path?: string; from?: string; to?: string };
  preserve: string[];
  unchanged: boolean;
  diff: { changed?: string[]; unchanged?: string[] };
};

export type RunSummary = {
  id: string;
  mode?: string | null;
  gate_passed?: boolean | null;
  summary?: Record<string, number>;
  identity?: Record<string, unknown>;
  mtime?: number;
};

export type CaseSummary = {
  id: string;
  version: number;
  work: string;
  history: string;
  step_count: number;
  check_counts: { must: number; quality: number; human: number };
  source: string;
};

export type CaseDetail = {
  id: string;
  version: number;
  work: string;
  work_dir: string;
  history: string;
  source: string;
  steps: { author: string; focus?: string | null }[];
  expect: {
    region: { path: string; from: string; to: string };
    preserve: string[];
  };
  checks: { id: string; level: string; question?: string | null }[];
  check_counts: { must: number; quality: number; human: number };
};

export type CaseRunLink = RunSummary & {
  case_id: string;
  attempt_count: number;
  outcomes: string[];
};

export type JobPayload = {
  id: string;
  status: string;
  case_ids: string[];
  mode: string;
  repeat: number;
  base_url: string;
  max_requests: number;
  out_name: string;
  run_id: string;
  created_at: string;
  finished_at?: string | null;
  completed_attempts: number;
  total_attempts: number;
  current_case?: string | null;
  current_attempt?: number | null;
  error?: string | null;
  gate_passed?: boolean | null;
  log: string[];
  baseline?: string | null;
};

function runBase(runId: string) {
  return `/api/runs/${encodeURIComponent(runId)}`;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const err = (data as { error?: { code?: string; where?: string; why?: string; fix?: string } }).error || {};
    throw new Error(
      `[${err.code || response.status}] ${err.where || path}：${err.why || "请求失败"}。建议：${err.fix || "重试"}`,
    );
  }
  return data as T;
}

export const fetchRuns = () => api<{ runs: RunSummary[]; runs_root: string }>("/api/runs");
export const fetchReport = (runId: string) => api<Report>(`${runBase(runId)}/report`);
export const fetchReviews = (runId: string) =>
  api<{ reviews: Review[]; attributions: Attribution[] }>(`${runBase(runId)}/reviews`);
export const saveReviews = (runId: string, payload: { reviews: Review[]; attributions: Attribution[] }) =>
  api<{ reviews: Review[]; attributions: Attribution[] }>(`${runBase(runId)}/reviews`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const mergeScore = (runId: string) =>
  api<Report>(`${runBase(runId)}/score`, { method: "POST", body: "{}" });
export const fetchAttempt = (runId: string, caseId: string, attempt: number) =>
  api<AttemptPayload>(`${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}`);
export const fetchTrace = (runId: string, caseId: string, attempt: number, turnId: string) =>
  api<TurnTrace>(
    `${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}/traces/${encodeURIComponent(turnId)}`,
  );
export const fetchDiff = (runId: string, caseId: string, attempt: number) =>
  api<DiffView>(`${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}/diff`);
export const fetchEvent = (runId: string, caseId: string, attempt: number, eventId: string) =>
  api<Record<string, unknown>>(
    `${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}/events/${encodeURIComponent(eventId)}`,
  );
export const fetchInput = (runId: string, caseId: string, attempt: number, operationId: string) =>
  api<unknown>(
    `${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}/inputs/${encodeURIComponent(operationId)}`,
  );
export const fetchCapture = (runId: string, caseId: string, attempt: number) =>
  api<{ capture_inputs: boolean }>(
    `${runBase(runId)}/attempts/${encodeURIComponent(caseId)}/${attempt}/capture`,
  );

export const fetchCases = () =>
  api<{ cases: CaseSummary[]; cases_root: string; works_root: string }>("/api/cases");
export const fetchCase = (caseId: string) =>
  api<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}`);
export const fetchCaseRuns = (caseId: string) =>
  api<{ case_id: string; runs: CaseRunLink[] }>(
    `/api/cases/${encodeURIComponent(caseId)}/runs`,
  );

export const fetchJobs = () =>
  api<{ jobs: JobPayload[]; active_id: string | null }>("/api/jobs");
export const fetchJob = (jobId: string) => api<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}`);
export const createJob = (body: {
  case_ids: string[];
  mode: "smoke" | "gate";
  repeat: number;
  base_url: string;
  max_requests: number;
  out_name?: string;
  baseline?: string;
  timeout?: number;
}) =>
  api<JobPayload>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const cancelJob = (jobId: string) =>
  api<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: "{}",
  });
