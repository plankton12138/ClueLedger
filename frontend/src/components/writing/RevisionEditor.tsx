/**
 * 稿纸修订模式（v1.2）。
 *
 * 作者在这里亲手改一章正文：编辑与「看差异」随时切换，点「确认修改」才入稿。
 * 未确认的文字按章节保存在本地草稿里，切换章节、刷新页面后仍在；确认前的正文
 * 仍以服务端版本为准，冲突时保留草稿并让作者选择用哪一份。
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { diffParagraphs, type DiffBlock } from "../../lib/proseDiff";
import { useWritingStore, writingDraftKey } from "../../state/writing";
import { Button } from "../ui/Button";

function Inline({ block }: { block: Extract<DiffBlock, { kind: "replace" }> }) {
  return (
    <>
      <p className="revision-diff-line">
        {block.inline.filter(span => span.kind !== "insert").map((span, index) =>
          span.kind === "delete" ? (
            <del key={index} className="revision-del">{span.text}</del>
          ) : (
            <span key={index}>{span.text}</span>
          ),
        )}
      </p>
      <p className="revision-diff-line">
        {block.inline.filter(span => span.kind !== "delete").map((span, index) =>
          span.kind === "insert" ? (
            <ins key={index} className="revision-ins">{span.text}</ins>
          ) : (
            <span key={index}>{span.text}</span>
          ),
        )}
      </p>
    </>
  );
}

function DiffView({ blocks }: { blocks: DiffBlock[] }) {
  const changed = blocks.some(block => block.kind !== "equal");
  if (!changed) {
    return <p className="revision-diff-empty">正文还没有改动。</p>;
  }
  return (
    <ul className="revision-diff" aria-label="正文差异">
      {blocks.map((block, index) => {
        if (block.kind === "equal") {
          return (
            <li key={index} className="revision-diff-equal">
              <p className="revision-diff-line">{block.text}</p>
            </li>
          );
        }
        if (block.kind === "replace") {
          return (
            <li key={index} className="revision-diff-block">
              <span className="revision-diff-tag">改写</span>
              <Inline block={block} />
            </li>
          );
        }
        if (block.kind === "delete") {
          return (
            <li key={index} className="revision-diff-block">
              <span className="revision-diff-tag">删除</span>
              <p className="revision-diff-line"><del className="revision-del">{block.before}</del></p>
            </li>
          );
        }
        return (
          <li key={index} className="revision-diff-block">
            <span className="revision-diff-tag">新增</span>
            <p className="revision-diff-line"><ins className="revision-ins">{block.after}</ins></p>
          </li>
        );
      })}
    </ul>
  );
}

export default function RevisionEditor({
  workspaceId,
  chapterId,
  onExit,
  onSaved,
}: {
  workspaceId: string;
  chapterId: string;
  onExit: () => void;
  onSaved: (revisionId: string | null) => void;
}) {
  const key = writingDraftKey(workspaceId, chapterId);
  const draft = useWritingStore(state => state.drafts[key]);
  const updateText = useWritingStore(state => state.updateText);
  const save = useWritingStore(state => state.save);
  const discardLocal = useWritingStore(state => state.discardLocal);
  const adoptServer = useWritingStore(state => state.adoptServer);
  const overrideWithLocal = useWritingStore(state => state.overrideWithLocal);
  const [mode, setMode] = useState<"edit" | "diff">("edit");
  const [busy, setBusy] = useState<"save" | "discard" | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const blocks = useMemo(
    () => diffParagraphs(draft?.baseText ?? "", draft?.text ?? ""),
    [draft?.baseText, draft?.text],
  );
  const changes = blocks.filter(block => block.kind !== "equal").length;

  useLayoutEffect(() => {
    const node = textarea.current;
    if (!node || mode !== "edit") return;
    // 量高度时 textarea 先缩到 320px，纸面 scrollTop 会被钳到 0；记下再还原，避免输入时把正在改的一行甩出视野。
    const scroller = node.closest<HTMLElement>(".paper-sheet");
    const top = scroller?.scrollTop ?? 0;
    node.style.height = "auto";
    node.style.height = `${Math.max(node.scrollHeight, 320)}px`;
    if (scroller) scroller.scrollTop = top;
  }, [mode, draft?.text]);

  if (!draft) {
    return (
      <div className="revision-editor" role="status">
        <p className="p-6 text-sm text-sub">正文还在读取，稍后再进入修订。</p>
      </div>
    );
  }

  const dirty = draft.dirty;
  const conflict = draft.conflict;

  async function confirm() {
    setBusy("save");
    setError(null);
    const ok = await save(workspaceId, chapterId);
    setBusy(null);
    if (ok) {
      const latest = useWritingStore.getState().drafts[key];
      onSaved(latest?.lastRevisionId ?? null);
      return;
    }
    const latest = useWritingStore.getState().drafts[key];
    if (latest?.error) setError(latest.error);
  }

  async function keepLocal() {
    setBusy("save");
    setError(null);
    const ok = await overrideWithLocal(workspaceId, chapterId);
    setBusy(null);
    if (ok) {
      const latest = useWritingStore.getState().drafts[key];
      onSaved(latest?.lastRevisionId ?? null);
    } else {
      const latest = useWritingStore.getState().drafts[key];
      if (latest?.error) setError(latest.error);
    }
  }

  async function useServer() {
    setBusy("discard");
    setError(null);
    await adoptServer(workspaceId, chapterId);
    setBusy(null);
    setMode("edit");
  }

  function requestExit() {
    if (dirty) {
      setAsking(true);
      return;
    }
    onExit();
  }

  function discardAndExit() {
    setAsking(false);
    setBusy("discard");
    void discardLocal(workspaceId, chapterId).finally(() => {
      setBusy(null);
      onExit();
    });
  }

  return (
    <div className="revision-editor flex min-h-0 flex-1 flex-col">
      <div className="revision-bar">
        <div className="revision-modes" role="group" aria-label="修订视图">
          <button
            type="button"
            className={mode === "edit" ? "active" : ""}
            aria-pressed={mode === "edit"}
            onClick={() => setMode("edit")}
          >
            编辑
          </button>
          <button
            type="button"
            className={mode === "diff" ? "active" : ""}
            aria-pressed={mode === "diff"}
            onClick={() => setMode("diff")}
          >
            看差异{changes > 0 ? ` · ${changes} 处` : ""}
          </button>
        </div>
        <span className="revision-state" role="status">
          {conflict
            ? "正文已更新，需要选择保留哪一份"
            : draft.saving
              ? "正在入稿…"
              : dirty
                ? `${draft.text.length} 字符 · 尚未入稿`
                : draft.savedAt
                  ? "已入稿"
                  : `${draft.text.length} 字符`}
        </span>
        <div className="flex-1" />
        <Button size="compact" onClick={requestExit} disabled={busy !== null}>取消</Button>
        <Button
          size="compact"
          variant="primary"
          onClick={() => void confirm()}
          disabled={!dirty || Boolean(conflict) || busy !== null || draft.saving}
          disabledReason={conflict ? "先处理正文冲突" : "正文没有改动"}
        >
          {busy === "save" ? "正在保存…" : "确认修改"}
        </Button>
      </div>

      {asking && (
        <div className="revision-conflict" role="alert">
          <p>这次修改还没有入稿。取消会丢弃它。</p>
          <div className="revision-conflict-actions">
            <Button size="compact" onClick={() => setAsking(false)}>继续编辑</Button>
            <Button size="compact" variant="primary" onClick={discardAndExit} disabled={busy !== null}>
              丢弃修改
            </Button>
          </div>
        </div>
      )}

      {conflict && (
        <div className="revision-conflict" role="alert">
          <p>这一章在你编辑期间被更新了。你的文字仍保留在这里，请选择保留哪一份。</p>
          <div className="revision-conflict-actions">
            <Button size="compact" onClick={() => void useServer()} disabled={busy !== null}>
              丢弃我的文字，改用最新正文
            </Button>
            <Button size="compact" variant="primary" onClick={() => void keepLocal()} disabled={busy !== null}>
              保留我的文字并覆盖
            </Button>
          </div>
        </div>
      )}

      {error && <p className="revision-error" role="alert">{error}</p>}

      <div className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm">
        <div className="paper-lined min-h-full px-8 py-6">
          {mode === "edit" ? (
            <textarea
              ref={textarea}
              aria-label="正文编辑"
              className="revision-textarea"
              value={draft.text}
              spellCheck={false}
              onChange={event => updateText(workspaceId, chapterId, event.target.value, false, true)}
            />
          ) : (
            <DiffView blocks={blocks} />
          )}
        </div>
      </div>
    </div>
  );
}
