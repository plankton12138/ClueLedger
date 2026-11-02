/**
 * 正文编辑器（1.0.9 P4；P6 取消章节构思门槛）。
 *
 * 章节建立即可写：空正文只有三个入口——自己写第一段、由模型起草、看看方向建议。
 * 方向建议只是旁置参考文字，不进入正式数据。页边索引显示已写入信息、正文新增候选与
 * 需要对齐项；作者确认候选后由服务端联合提交信息线、信息变化与绑定。
 * 保存只以正文摘要为前提，不再携带章节修订。
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { PenLine, Save } from "lucide-react";

import type {
  ChapterView,
  InformationIndexItem,
  InformationOccurrence,
  ModelSupportView,
} from "../../api/writing";
import { writingKey } from "../../api/writing";
import { useWritingStore, writingDraftKey } from "../../state/writing";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import ChapterMarginIndex from "./ChapterMarginIndex";
import ModelCandidate from "./ModelCandidate";

function proseParts(text: string): { prefix: string; body: string } {
  const match = /^(#\s[^\n]*(?:\n{1,2}|$))/.exec(text);
  if (!match) return { prefix: "", body: text };
  return { prefix: match[1], body: text.slice(match[1].length) };
}

function candidateBody(text: string): string {
  return proseParts(text).body.trim();
}

function savedLabel(value: string | null): string {
  if (!value) return "已保存";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已保存";
  return `已保存 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

export default function ChapterEditor({
  workspaceId,
  chapter,
  support,
  informationId,
}: {
  workspaceId: string;
  chapter: ChapterView;
  support: ModelSupportView;
  informationId?: string | null;
}) {
  const plan = chapter.plan;
  const prose = chapter.prose;
  const chapterId = plan?.id ?? "";
  const key = writingDraftKey(workspaceId, chapterId);
  const draft = useWritingStore(state => state.drafts[key]);
  const updateText = useWritingStore(state => state.updateText);
  const save = useWritingStore(state => state.save);
  const adoptServer = useWritingStore(state => state.adoptServer);
  const overrideWithLocal = useWritingStore(state => state.overrideWithLocal);
  const assistStart = useWritingStore(state => state.assistStart);
  const confirmCandidate = useWritingStore(state => state.confirmCandidate);
  const reviewIndex = useWritingStore(state => state.reviewIndex);
  const assist = useWritingStore(state => state.assists[`prose:${chapterId}`]);
  const direction = useWritingStore(state => state.assists[`direction:${chapterId}`]);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const pendingSelection = useRef<{ start: number; end: number } | null>(null);
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [showServerText, setShowServerText] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [emptyNoticeDismissed, setEmptyNoticeDismissed] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const text = draft?.text ?? prose?.text ?? "";
  const parts = useMemo(() => proseParts(text), [text]);
  const editorText = parts.body;
  const dirty = Boolean(draft?.dirty);
  const saving = Boolean(draft?.saving);
  const conflict = draft?.conflict ?? null;
  const assistKey = `prose:${chapterId}`;
  const directionKey = `direction:${chapterId}`;
  const supports = (kind: string) => support.supported_assists.length === 0 || support.supported_assists.includes(kind);
  const supported = support.available && (supports("prose_draft") || supports("prose_continue"));
  const directionSupported = support.available && supports("chapter_direction");
  const assisted = assist && assist.status !== "idle" ? assist.text : "";
  const emptyBody = editorText.trim().length === 0;
  const assistKind = emptyBody ? "prose_draft" : "prose_continue";
  const assistLabel = emptyBody ? "由模型起草" : "让模型续写";
  const disabledReason = !support.available
    ? support.disabled_reason ?? "当前没有可用模型"
    : !supported ? "当前模型没有开放这类辅助"
    : dirty || saving ? "先保存当前修改，再让模型续写" : undefined;
  const directionDisabledReason = !support.available
    ? support.disabled_reason ?? "当前没有可用模型"
    : !directionSupported ? "当前模型没有开放这类辅助" : undefined;
  const indexItems = chapter.index?.items ?? [];
  // 空章提示看的是「已核对的信息」，不等语义分析跑完：分析可能还在排队，
  // 而作者此刻需要的是可以忽略的反馈，不是等到候选回来才出现的提醒。
  const emptyChapterNotice = !emptyBody && !dirty && !saving && !conflict
    && indexItems.length === 0 && !emptyNoticeDismissed;

  useEffect(() => {
    setActiveItemId(null);
    setShowServerText(false);
    setEmptyNoticeDismissed(false);
  }, [chapterId]);

  useEffect(() => {
    if (!informationId || !chapter.index) return;
    const item = chapter.index.items.find(entry => entry.item_id === informationId);
    if (!item) return;
    const occurrence = item.occurrences.find(entry => entry.start !== null);
    locate(item, occurrence ?? item.occurrences[0] ?? { path: "", digest: "", quote: "", start: null, end: null, state: "unavailable" });
  }, [informationId, chapter.index, dirty]);

  useLayoutEffect(() => {
    const range = pendingSelection.current;
    const element = textarea.current;
    if (!range || !element) return;
    element.focus();
    element.setSelectionRange(range.start, range.end);
    pendingSelection.current = null;
  }, [text]);

  function change(value: string) {
    updateText(workspaceId, chapterId, value, !composing.current);
  }

  function changeBody(value: string) {
    change(`${parts.prefix}${value}`);
  }

  function rememberSelection() {
    const element = textarea.current;
    if (!element || !chapter.index) return;
    const position = parts.prefix.length + element.selectionStart;
    const found = chapter.index.items.find(item =>
      item.placement === "written" &&
      item.occurrences.some(occurrence => occurrence.start !== null && occurrence.end !== null && position >= occurrence.start && position <= occurrence.end),
    );
    setActiveItemId(found?.item_id ?? null);
  }

  function locate(item: InformationIndexItem, occurrence: InformationOccurrence) {
    setActiveItemId(item.item_id);
    // 本地草稿与索引引用的服务端正文可能不同；未保存时只高亮条目，不移动光标。
    if (occurrence.start === null || dirty) return;
    const end = occurrence.end ?? occurrence.start + occurrence.quote.length;
    const startInBody = occurrence.start - parts.prefix.length;
    const endInBody = end - parts.prefix.length;
    if (startInBody < 0 || endInBody < startInBody) return;
    pendingSelection.current = { start: startInBody, end: endInBody };
    const element = textarea.current;
    element?.focus();
    element?.setSelectionRange(startInBody, endInBody);
    pendingSelection.current = null;
  }

  function insertAtCursor(value: string) {
    const insertion = candidateBody(value);
    if (!insertion) return;
    const element = textarea.current;
    const start = element?.selectionStart ?? editorText.length;
    const end = element?.selectionEnd ?? start;
    const next = `${editorText.slice(0, start)}${insertion}${editorText.slice(end)}`;
    pendingSelection.current = { start: start + insertion.length, end: start + insertion.length };
    changeBody(next);
  }

  function replaceAll(value: string) {
    const body = candidateBody(value);
    if (!body) return;
    pendingSelection.current = null;
    changeBody(body);
  }

  function startAssist() {
    if (!plan || !prose) return;
    void assistStart(workspaceId, assistKey, {
      kind: assistKind,
      target: { kind: "prose", workspace_id: workspaceId, chapter_id: chapterId },
      base: {
        plan_revision: chapter.index?.plan_revision,
        prose_path: prose.path,
        prose_digest: prose.digest,
      },
      request_key: writingKey(`assist-${assistKind}-${chapterId}`),
    });
  }

  function startDirection() {
    if (!plan) return;
    void assistStart(workspaceId, directionKey, {
      kind: "chapter_direction",
      target: { kind: "chapter_direction", workspace_id: workspaceId, chapter_id: chapterId },
      base: { plan_revision: chapter.index?.plan_revision },
      request_key: writingKey(`assist-direction-${chapterId}`),
    });
  }

  async function review() {
    setReviewing(true);
    try {
      await reviewIndex(workspaceId, chapterId);
    } finally {
      setReviewing(false);
    }
  }

  async function confirm(item: InformationIndexItem) {
    if (!item.candidate_id) return;
    setConfirmingId(item.item_id);
    try {
      await confirmCandidate(workspaceId, chapterId, item.candidate_id);
    } finally {
      setConfirmingId(null);
    }
  }

  if (!plan || !prose) return <StatusMessage tone="error">这一章的正文不存在。刷新工作台后重新选择章节。</StatusMessage>;

  const status = conflict
    ? "保存冲突"
    : saving
      ? "保存中…"
      : draft?.error
        ? "保存失败"
        : dirty
          ? "未保存"
          : savedLabel(draft?.savedAt ?? null);

  return <div className="chapter-sheet">
    <div className="chapter-sheet-grid">
      <div className="chapter-editor">
        <header className="chapter-editor-bar">
          <div className="chapter-editor-title">
            <h1>第{plan.order + 1}章{plan.title ? ` · ${plan.title}` : ""}</h1>
            <span className={`chapter-editor-status ${conflict ? "conflict" : dirty || draft?.error ? "dirty" : ""}`} role="status">{status}</span>
          </div>
          <div className="chapter-editor-actions">
            <Button size="compact" onClick={() => void save(workspaceId, chapterId)} disabled={!dirty || saving || Boolean(conflict)}>
              <Save size={15} strokeWidth={1.8} aria-hidden="true" />保存
            </Button>
          </div>
        </header>

        {conflict && <StatusMessage tone="warning" action={<Button size="compact" onClick={() => setShowServerText(value => !value)}>{showServerText ? "收起服务端版本" : "对比服务端版本"}</Button>}>
          <div>
            <p>正文在保存前已被修改，本地文字仍保留。</p>
            <div className="chapter-conflict-actions">
              <Button size="compact" onClick={() => void adoptServer(workspaceId, chapterId)}>载入服务端版本</Button>
              <Button size="compact" variant="danger" onClick={() => void overrideWithLocal(workspaceId, chapterId)}>用我的版本覆盖</Button>
            </div>
            {showServerText && <pre className="chapter-conflict-text">{conflict.serverText}</pre>}
          </div>
        </StatusMessage>}
        {draft?.error && !conflict && <StatusMessage tone="error" action={<Button size="compact" onClick={() => void save(workspaceId, chapterId)}>重试保存</Button>}>{draft.error}</StatusMessage>}

        {!emptyBody && <div className="chapter-editor-assist">
          <ModelCandidate
            assistKey={assistKey}
            support={{ ...support, available: supported }}
            label={assistLabel}
            disabledReason={disabledReason}
            onStart={startAssist}
            onAdopt={value => replaceAll(value)}
            adoptLabel="替换草稿"
            extraAdopt={value => insertAtCursor(value)}
            extraAdoptLabel="插入光标"
          />
          {assisted && <p className="ui-field-hint">候选保留在旁置区；采用后先成为本地草稿，保存成功才进入索引。</p>}
        </div>}

        {emptyBody && !conflict && <section className="chapter-empty-start" aria-label="开始正文">
          <div>
            <h2>正文还没有内容</h2>
            <p>直接写第一段；也可以让模型起草或先看几个推进方向。</p>
          </div>
          <div className="chapter-empty-actions">
            <Button variant="primary" onClick={() => textarea.current?.focus()}>
              <PenLine size={15} strokeWidth={1.9} aria-hidden="true" />自己写第一段
            </Button>
            <ModelCandidate
              assistKey={assistKey}
              support={{ ...support, available: supported }}
              label="由模型起草"
              disabledReason={disabledReason}
              onStart={startAssist}
              onAdopt={value => replaceAll(value)}
              adoptLabel="采用为正文草稿"
            />
            <ModelCandidate
              assistKey={directionKey}
              support={{ ...support, available: directionSupported }}
              label="看看方向建议"
              disabledReason={directionDisabledReason}
              onStart={startDirection}
              onAdopt={() => undefined}
              readOnly
            />
          </div>
          {assisted && <p className="ui-field-hint">候选采用后先进入下方稿纸，保存成功才会更新正文与信息索引。</p>}
          {direction && direction.status !== "idle" && direction.text && <p className="ui-field-hint">方向建议只是参考文字，不进入信息线，也不会自动写入正文。</p>}
        </section>}

        {emptyChapterNotice && <StatusMessage tone="warning" action={
          <div className="chapter-empty-feedback-actions">
            <Button size="compact" busy={reviewing} onClick={() => void review()}>让模型看看本章</Button>
            <Button size="compact" variant="quiet" onClick={() => setEmptyNoticeDismissed(true)}>暂时忽略</Button>
          </div>
        }>
          本章还没有可核对的信息变化。可以继续写，也可以让模型复看是否有遗漏的线索或伏笔。
        </StatusMessage>}

        <textarea
          ref={textarea}
          className="chapter-editor-textarea"
          aria-label="正文编辑"
          data-empty={emptyBody || undefined}
          placeholder="直接写第一段。正文会自动保存。"
          value={editorText}
          spellCheck={false}
          onChange={event => changeBody(event.target.value)}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={event => { composing.current = false; updateText(workspaceId, chapterId, `${parts.prefix}${event.currentTarget.value}`); }}
          onSelect={rememberSelection}
          onBlur={() => { if (dirty && !conflict && !composing.current) void save(workspaceId, chapterId); }}
          onKeyDown={event => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
              event.preventDefault();
              if (dirty && !conflict) void save(workspaceId, chapterId);
            }
          }}
        />
      </div>

      {chapter.index && <div className="chapter-margin-inline">
        <ChapterMarginIndex
          index={chapter.index}
          activeItemId={activeItemId}
          onLocate={locate}
          onConfirm={item => void confirm(item)}
          confirmingId={confirmingId}
        />
      </div>}
    </div>
  </div>;
}
