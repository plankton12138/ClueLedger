/**
 * 账本编辑对话框（V03-3.2）：表单字段来自服务端 schema，保存后回读真实版本。
 *
 * 三条约束：
 * - 未确认的假设不在这里保存（种类里没有「假设」）；角色猜测用「认知 / 证词」。
 * - 修订带回读到的 revision；服务端发现已被别处修改时拒绝覆盖，并提供重新读取。
 * - 同一份内容的重试复用 request_id；改动字段后换新 id，避免把编辑当成重试。
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";

import {
  createLedgerEntry,
  fetchLedger,
  fetchLedgerEntry,
  fetchLedgerSchema,
  stageLedgerCandidate,
  updateLedgerEntry,
  type LedgerCandidateGroup,
  type LedgerEntry,
  type LedgerField,
  type LedgerKind,
  type LedgerSaveResult,
} from "../../api/ledger";
import { V2Error } from "../../api/v2";
import { message } from "../../state/v2";
import { ErrorText } from "../Workbench";

type FieldValue = string | boolean | string[];

function newRequestId(): string {
  return crypto.randomUUID();
}

function initialValues(kind: LedgerKind | undefined, content: Record<string, unknown>) {
  const values: Record<string, FieldValue> = {};
  for (const field of kind?.fields ?? []) {
    const value = content[field.key];
    if (field.type === "checkbox") values[field.key] = Boolean(value);
    else if (field.type === "refs") values[field.key] = Array.isArray(value) ? value.map(String) : [];
    else if (field.type === "lines") {
      values[field.key] = Array.isArray(value) ? value.map(String).join("\n") : "";
    } else values[field.key] = typeof value === "string" ? value : value == null ? "" : String(value);
  }
  return values;
}

function toFields(kind: LedgerKind, values: Record<string, FieldValue>): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  for (const field of kind.fields) {
    const value = values[field.key];
    if (field.type === "checkbox") fields[field.key] = Boolean(value);
    else if (field.type === "refs") fields[field.key] = Array.isArray(value) ? value : [];
    else if (field.type === "lines") {
      fields[field.key] = String(value ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    } else fields[field.key] = String(value ?? "").trim();
  }
  return fields;
}

function missingRequired(kind: LedgerKind, fields: Record<string, unknown>): string | null {
  for (const field of kind.fields) {
    if (!field.required) continue;
    const value = fields[field.key];
    if (value === "" || value == null) return `请填写「${field.label}」`;
  }
  return null;
}

export default function LedgerEditor({
  projectId,
  entryId,
  onClose,
  onSaved,
  onStaged,
}: {
  projectId: string;
  entryId?: string;
  onClose(): void;
  onSaved(result: LedgerSaveResult): void;
  onStaged?(group: LedgerCandidateGroup): void;
}) {
  const [schema, setSchema] = useState<LedgerKind[]>([]);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [selectedKind, setSelectedKind] = useState("");
  const [revision, setRevision] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, FieldValue>>({});
  const [requestId, setRequestId] = useState(newRequestId);
  const [hypothesis, setHypothesis] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [ready, setReady] = useState(false);

  const form = useMemo(
    () => schema.find((item) => item.kind === selectedKind),
    [schema, selectedKind],
  );

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [kinds, rows] = await Promise.all([
          fetchLedgerSchema(projectId),
          fetchLedger(projectId),
        ]);
        if (cancelled) return;
        setSchema(kinds);
        setEntries(rows);
        if (entryId) {
          const detail = await fetchLedgerEntry(projectId, entryId);
          if (cancelled) return;
          setSelectedKind(detail.kind);
          setRevision(detail.revision_id);
          setValues(initialValues(kinds.find((item) => item.kind === detail.kind), detail.content));
        } else {
          const first = kinds[0];
          setSelectedKind(first?.kind ?? "");
          setValues(initialValues(first, {}));
        }
        setReady(true);
      } catch (e) {
        if (!cancelled) setError(message(e));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [projectId, entryId]);

  function changeKind(kind: string) {
    setSelectedKind(kind);
    setValues(initialValues(schema.find((item) => item.kind === kind), {}));
    setRequestId(newRequestId());
    setError(null);
    setConflict(false);
  }

  function setValue(field: LedgerField, value: FieldValue) {
    setValues((current) => ({ ...current, [field.key]: value }));
    // 内容变了就是新意图：换幂等键，避免服务端把编辑当成旧请求的重试。
    setRequestId(newRequestId());
  }

  async function reloadLatest() {
    if (!entryId) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await fetchLedgerEntry(projectId, entryId);
      setRevision(detail.revision_id);
      setValues(initialValues(form, detail.content));
      setConflict(false);
      setRequestId(newRequestId());
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!form) return;
    const fields = toFields(form, values);
    const missing = missingRequired(form, fields);
    if (missing) {
      setError(missing);
      return;
    }
    setBusy(true);
    setError(null);
    setConflict(false);
    try {
      const result = entryId
        ? await updateLedgerEntry(projectId, entryId, {
            fields,
            expected_revision: revision ?? "",
            request_id: requestId,
          })
        : await createLedgerEntry(projectId, {
            kind: form.kind,
            fields,
            request_id: requestId,
          });
      onSaved(result);
      onClose();
    } catch (e) {
      if (e instanceof V2Error && e.code === "LEDGER_ENTRY_CONFLICT") {
        setConflict(true);
        setError("这条设定已被别处修改，当前内容基于旧版本；重新读取后再保存，不会覆盖新版本。");
      } else {
        setError(message(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function stage() {
    if (!form) return;
    const fields = toFields(form, values);
    const missing = missingRequired(form, fields);
    if (missing) {
      setError(missing);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const group = await stageLedgerCandidate(projectId, {
        kind: form.kind,
        fields,
        hypothesis,
        request_id: requestId,
      });
      onStaged?.(group);
      onClose();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  const description = entryId
    ? "直接修改会形成新版本；保存后回读实际版本。角色猜测与证词请记在「认知 / 证词」，不要写成事实。"
    : "「保存设定」直接写入正式账本；「存为待确认」只进候选组，确认后才入账。假设不会进入正式账本。";

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[min(640px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col p-6">
          <Dialog.Title className="font-serif text-xl text-fgStrong">
            {entryId ? "编辑设定" : "新增设定"}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs leading-6 text-sub">
            {description}
          </Dialog.Description>

          <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            {!entryId && (
              <label className="block text-xs">
                <span className="text-sub">种类</span>
                <select
                  className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                  value={selectedKind}
                  onChange={(e) => changeKind(e.target.value)}
                  aria-label="设定种类"
                >
                  {schema.map((item) => (
                    <option key={item.kind} value={item.kind}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {ready && form ? (
              form.fields.map((field) => (
                <LedgerFieldInput
                  key={field.key}
                  field={field}
                  value={values[field.key]}
                  entries={entries}
                  onChange={(value) => setValue(field, value)}
                />
              ))
            ) : (
              <p className="text-xs text-sub">正在读取表单…</p>
            )}

            {conflict && (
              <button
                className="btn btn-gray px-3 py-1.5 text-xs"
                disabled={busy}
                onClick={() => void reloadLatest()}
              >
                重新读取最新版本
              </button>
            )}
            <ErrorText>{error}</ErrorText>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
            {!entryId && (
              <label className="mr-auto flex items-center gap-2 text-xs text-sub">
                <input
                  type="checkbox"
                  checked={hypothesis}
                  onChange={(e) => setHypothesis(e.target.checked)}
                />
                这只是假设（不进入正式账本）
              </label>
            )}
            <button className="btn btn-gray px-4 py-2 text-xs" disabled={busy} onClick={onClose}>
              取消
            </button>
            {!entryId && (
              <button
                className="btn btn-gray px-4 py-2 text-xs"
                disabled={busy || !ready || !form}
                onClick={() => void stage()}
              >
                {busy ? "处理中…" : "存为待确认"}
              </button>
            )}
            <button
              className="btn px-4 py-2 text-xs"
              disabled={busy || !ready || !form}
              onClick={() => void save()}
            >
              {busy ? "保存中…" : entryId ? "保存修改" : "保存设定"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function LedgerFieldInput({
  field,
  value,
  entries,
  onChange,
}: {
  field: LedgerField;
  value: FieldValue | undefined;
  entries: LedgerEntry[];
  onChange(value: FieldValue): void;
}) {
  const label = (
    <span className="text-sub">
      {field.label}
      {field.required ? "（必填）" : ""}
    </span>
  );

  if (field.type === "checkbox") {
    return (
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }
  if (field.type === "select") {
    return (
      <label className="block text-xs">
        {label}
        <select
          className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "ref") {
    const options = refOptions(entries, field);
    return (
      <label className="block text-xs">
        {label}
        <select
          className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">（未指定）</option>
          {options.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.title} · {entry.kind_label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "refs") {
    const options = refOptions(entries, field);
    const selected = Array.isArray(value) ? value : [];
    return (
      <fieldset className="text-xs">
        <legend className="text-sub">{field.label}</legend>
        {options.length === 0 ? (
          <p className="mt-1 text-[11px] text-sub">还没有可选的{refLabels(field)}，先保存对应条目。</p>
        ) : (
          <div className="mt-1 max-h-32 space-y-1 overflow-y-auto rounded border border-line px-2 py-1.5">
            {options.map((entry) => (
              <label key={entry.id} className="flex items-center gap-2 leading-6">
                <input
                  type="checkbox"
                  checked={selected.includes(entry.id)}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...selected, entry.id]
                        : selected.filter((id) => id !== entry.id),
                    )
                  }
                />
                <span>
                  {entry.title}
                  <span className="text-[10px] text-sub"> · {entry.kind_label}</span>
                </span>
              </label>
            ))}
          </div>
        )}
      </fieldset>
    );
  }
  if (field.type === "textarea" || field.type === "lines") {
    return (
      <label className="block text-xs">
        {label}
        <textarea
          className="mt-1 w-full resize-none rounded border border-line bg-transparent px-3 py-2 leading-6"
          rows={field.type === "lines" ? 3 : 2}
          value={String(value ?? "")}
          placeholder={field.type === "lines" ? "一行一条，空行忽略" : ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
    );
  }
  return (
    <label className="block text-xs">
      {label}
      <input
        className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function refOptions(entries: LedgerEntry[], field: LedgerField): LedgerEntry[] {
  const allowed = new Set(field.ref_kinds ?? []);
  return entries.filter((entry) => allowed.has(entry.kind));
}

function refLabels(field: LedgerField): string {
  return (field.ref_kinds ?? []).map((kind) => kind.split(".").pop() ?? kind).join("、");
}
