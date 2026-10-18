/**
 * 账本客户端（V03-3.2）：表单描述、条目列表/详情、直接保存与修订。
 *
 * 三个契约点：
 * - 表单字段来自服务端 `GET /ledger/schema`，与后端白名单同源，不在页面硬编码；
 * - 创建/修订都带 `request_id`，重试不会重复入账；
 * - 修订必须带回读到 `expected_revision`，保存后用响应里的新版本回读。
 */

import { projectPrefix, request } from "./v2";

export interface LedgerEntry {
  id: string;
  kind: string;
  kind_label: string;
  title: string;
  summary: string;
}

export interface LedgerEntryDetail extends LedgerEntry {
  revision_id: string;
  content: Record<string, unknown>;
}

export type LedgerFieldType =
  | "text"
  | "textarea"
  | "select"
  | "checkbox"
  | "ref"
  | "refs"
  | "lines";

export interface LedgerFieldOption {
  value: string;
  label: string;
}

export interface LedgerField {
  key: string;
  label: string;
  type: LedgerFieldType;
  required?: boolean;
  options?: LedgerFieldOption[];
  ref_kinds?: string[];
}

export interface LedgerKind {
  kind: string;
  label: string;
  fields: LedgerField[];
}

export interface LedgerSaveResult {
  status: string;
  commit_id: string | null;
  entry: LedgerEntryDetail;
  replayed: boolean;
}

export interface LedgerCandidateItem {
  item_id: string;
  kind: string;
  kind_label: string;
  title: string;
  summary: string;
  hypothesis: boolean;
  status: "pending" | "accepted" | "skipped";
  accepted_entry_id: string | null;
}

export interface LedgerCandidateGroup {
  group_id: string;
  project_id: string;
  revision: number;
  items: LedgerCandidateItem[];
  pending_count: number;
  hypothesis_count: number;
  updated_at: string;
}

export interface LedgerCandidateAcceptResult {
  status: string;
  commit_id: string;
  entries: LedgerEntryDetail[];
  commit_item_ids: string[];
  group: LedgerCandidateGroup;
}

export interface LedgerSaveInput {
  kind: string;
  fields: Record<string, unknown>;
  request_id: string;
}

export interface LedgerUpdateInput {
  fields: Record<string, unknown>;
  expected_revision: string;
  request_id: string;
}

export async function fetchLedger(projectId: string): Promise<LedgerEntry[]> {
  const payload = await request<{ entries: LedgerEntry[] }>(
    `${projectPrefix(projectId)}/ledger`,
  );
  return payload.entries;
}

export async function fetchLedgerSchema(projectId: string): Promise<LedgerKind[]> {
  const payload = await request<{ kinds: LedgerKind[] }>(
    `${projectPrefix(projectId)}/ledger/schema`,
  );
  return payload.kinds;
}

export async function fetchLedgerEntry(
  projectId: string,
  entryId: string,
): Promise<LedgerEntryDetail> {
  return request(
    `${projectPrefix(projectId)}/ledger/entries/${encodeURIComponent(entryId)}`,
  );
}

export async function createLedgerEntry(
  projectId: string,
  input: LedgerSaveInput,
): Promise<LedgerSaveResult> {
  return request(`${projectPrefix(projectId)}/ledger/entries`, input);
}

export async function updateLedgerEntry(
  projectId: string,
  entryId: string,
  input: LedgerUpdateInput,
): Promise<LedgerSaveResult> {
  return request(
    `${projectPrefix(projectId)}/ledger/entries/${encodeURIComponent(entryId)}`,
    input,
    "PATCH",
  );
}

export async function fetchLedgerCandidates(projectId: string): Promise<LedgerCandidateGroup> {
  return request(`${projectPrefix(projectId)}/ledger/candidates`);
}

export async function stageLedgerCandidate(
  projectId: string,
  input: { kind: string; fields: Record<string, unknown>; hypothesis: boolean; request_id: string },
): Promise<LedgerCandidateGroup> {
  return request(`${projectPrefix(projectId)}/ledger/candidates/items`, input);
}

export async function acceptLedgerCandidates(
  projectId: string,
  input: { item_ids: string[]; expected_revision: number; request_id: string },
): Promise<LedgerCandidateAcceptResult> {
  return request(`${projectPrefix(projectId)}/ledger/candidates/accept`, input);
}

export async function skipLedgerCandidates(
  projectId: string,
  input: { item_ids: string[]; expected_revision: number; request_id: string },
): Promise<LedgerCandidateGroup> {
  return request(`${projectPrefix(projectId)}/ledger/candidates/skip`, input);
}
