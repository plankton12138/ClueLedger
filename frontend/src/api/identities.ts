/**
 * 0.3 稿件身份契约（V03-0.2 冻结）——与后端
 * `src/hastings/domain/manuscript/identities.py` 逐字段对应。
 *
 * 不变量 I01：正式稿/修改副本/候选/任务快照身份不同；API 引用带类型，
 * 查询/写入拒绝混用。前端按 `kind` 分支，不解析中文错误消息（计划 §4.4）。
 *
 * wire 约定：snake_case（与 chat.ts 的 idempotency_key/expected_head 一致）；
 * 往返样例见 `__fixtures__/v03-identities.json`，由 identities.test.ts 比对。
 */

export type ManuscriptKind = "formal" | "edit_draft" | "candidate" | "task_snapshot";

/** 每类身份允许的 ID 前缀（与后端 ID_PREFIXES 一致）。 */
export const ID_PREFIXES: Record<ManuscriptKind, readonly string[]> = {
  formal: ["art", "rev", "cmt"],
  edit_draft: ["eds", "edc"],
  candidate: ["cnd"],
  task_snapshot: ["tks"],
};

/** 0.3 新增 ID 前缀总表（既有前缀一并登记）。 */
export const V03_ID_PREFIXES: Record<string, string> = {
  eds: "修改副本会话 EditSession",
  edc: "副本章节 DraftChapter",
  cnd: "候选 Candidate",
  tks: "任务快照 ContextSnapshot",
  op: "操作记录 OperationRecord",
};

export interface ManuscriptRef {
  kind: ManuscriptKind;
  id: string;
  /** formal：读取锚点（at_commit_id）。 */
  commitId?: string | null;
  /** edit_draft：模型引用冻结的保存序号。 */
  saveSeq?: number | null;
  /** candidate：采用时携带的预览内容哈希。 */
  contentHash?: string | null;
  /** task_snapshot：与 Job 绑定的不可变输入版本。 */
  contextVersion?: number | null;
}

/** wire 形状（snake_case）；解析后转 camelCase 消费。 */
export interface ManuscriptRefWire {
  kind: string;
  id: string;
  commit_id?: string | null;
  save_seq?: number | null;
  content_hash?: string | null;
  context_version?: number | null;
}

export type OperationStatus = "committed" | "no_change" | "blocked" | "failed";

export interface OperationEnvelope {
  operationId: string;
  status: OperationStatus;
  projectId: string;
  conversationId?: string | null;
  commitId?: string | null;
  changedChapterIds: string[];
  summaryState?: string | null;
  ledgerSuggestionsState?: string | null;
  resultUrl?: string | null;
}

export interface OperationEnvelopeWire {
  operation_id: string;
  status: string;
  project_id: string;
  conversation_id?: string | null;
  commit_id?: string | null;
  changed_chapter_ids?: string[];
  summary_state?: string | null;
  ledger_suggestions_state?: string | null;
  result_url?: string | null;
}

export interface ErrorEnvelope {
  code: string;
  category: "violation" | "legal_empty" | "environment";
  where: string;
  why: string;
  fix: string;
  details: Record<string, unknown>;
}

export interface ErrorEnvelopeWire {
  error: ErrorEnvelope;
}

// ---------------------------------------------------------------------------
// 纯函数：解析 / 守卫 / 序列化（前端按 code 与 kind 分支，不解析中文消息）
// ---------------------------------------------------------------------------

const REF_KINDS: readonly ManuscriptKind[] = ["formal", "edit_draft", "candidate", "task_snapshot"];

export function isManuscriptKind(value: unknown): value is ManuscriptKind {
  return typeof value === "string" && (REF_KINDS as readonly string[]).includes(value);
}

function prefixOf(id: string): string {
  const idx = id.indexOf("_");
  return idx > 0 ? id.slice(0, idx) : "";
}

/** 按 ID 前缀推断身份；无法识别返回 null。 */
export function kindOfId(id: string): ManuscriptKind | null {
  const prefix = prefixOf(id);
  for (const kind of REF_KINDS) {
    if (ID_PREFIXES[kind].includes(prefix)) return kind;
  }
  return null;
}

/** 引用是否属于该类身份（kind 与前缀双校验，I01）。 */
export function refMatchesKind(ref: ManuscriptRef, kind: ManuscriptKind): boolean {
  return ref.kind === kind && ID_PREFIXES[kind].includes(prefixOf(ref.id));
}

export function parseManuscriptRef(wire: ManuscriptRefWire): ManuscriptRef {
  if (!isManuscriptKind(wire.kind)) {
    throw new Error(`manuscript_ref.kind 未知：${String(wire.kind)}`);
  }
  if (!wire.id || wire.id.trim() !== wire.id) {
    throw new Error("manuscript_ref.id 为空或含首尾空白");
  }
  const ref: ManuscriptRef = { kind: wire.kind, id: wire.id };
  if (wire.commit_id != null) ref.commitId = wire.commit_id;
  if (wire.save_seq != null) ref.saveSeq = wire.save_seq;
  if (wire.content_hash != null) ref.contentHash = wire.content_hash;
  if (wire.context_version != null) ref.contextVersion = wire.context_version;
  return ref;
}

export function manuscriptRefToWire(ref: ManuscriptRef): ManuscriptRefWire {
  const wire: ManuscriptRefWire = { kind: ref.kind, id: ref.id };
  if (ref.commitId != null) wire.commit_id = ref.commitId;
  if (ref.saveSeq != null) wire.save_seq = ref.saveSeq;
  if (ref.contentHash != null) wire.content_hash = ref.contentHash;
  if (ref.contextVersion != null) wire.context_version = ref.contextVersion;
  return wire;
}

export function parseOperationEnvelope(wire: OperationEnvelopeWire): OperationEnvelope {
  for (const field of ["operation_id", "status", "project_id"] as const) {
    if (wire[field] == null) {
      throw new Error(`operation_envelope 缺少必填字段 ${field}`);
    }
  }
  const envelope: OperationEnvelope = {
    operationId: wire.operation_id,
    status: wire.status as OperationStatus,
    projectId: wire.project_id,
    changedChapterIds: [...(wire.changed_chapter_ids ?? [])],
  };
  if (wire.conversation_id != null) envelope.conversationId = wire.conversation_id;
  if (wire.commit_id != null) envelope.commitId = wire.commit_id;
  if (wire.summary_state != null) envelope.summaryState = wire.summary_state;
  if (wire.ledger_suggestions_state != null) envelope.ledgerSuggestionsState = wire.ledger_suggestions_state;
  if (wire.result_url != null) envelope.resultUrl = wire.result_url;
  return envelope;
}

export function operationEnvelopeToWire(envelope: OperationEnvelope): OperationEnvelopeWire {
  const wire: OperationEnvelopeWire = {
    operation_id: envelope.operationId,
    status: envelope.status,
    project_id: envelope.projectId,
    changed_chapter_ids: [...envelope.changedChapterIds],
  };
  if (envelope.conversationId != null) wire.conversation_id = envelope.conversationId;
  if (envelope.commitId != null) wire.commit_id = envelope.commitId;
  if (envelope.summaryState != null) wire.summary_state = envelope.summaryState;
  if (envelope.ledgerSuggestionsState != null) wire.ledger_suggestions_state = envelope.ledgerSuggestionsState;
  if (envelope.resultUrl != null) wire.result_url = envelope.resultUrl;
  return wire;
}
