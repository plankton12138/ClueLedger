/**
 * 全书构思（1.0.9 P4；P6 改为正文为主）。
 *
 * 空白作品从这里开始：写一句起点即可建立章节并直接写正文。这里同时给出信息线总览，
 * 作者看到的是跨章积累下来的信息，而不是需要预先填写的章节任务。
 */

import { useEffect, useState } from "react";
import { ArrowRight, BookOpenText, PenLine } from "lucide-react";

import { errorMessage } from "../../api/errors";
import { saveBookIntent, writingKey, type ModelSupportView, type WritingBookView } from "../../api/writing";
import { useWritingStore } from "../../state/writing";
import { Button } from "../ui/Button";
import ModelCandidate from "./ModelCandidate";

export default function BookPlanView({
  workspaceId,
  book,
  planRevision,
  support,
  onChanged,
  onCreateChapter,
  onSelectChapter,
}: {
  workspaceId: string;
  book: WritingBookView;
  planRevision: string | null;
  support: ModelSupportView;
  onChanged: () => Promise<void>;
  onCreateChapter: () => Promise<void>;
  onSelectChapter: (chapterId: string) => void;
}) {
  const [intent, setIntent] = useState(book.intent ?? "");
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assistStart = useWritingStore(state => state.assistStart);
  const assistKey = "book_intent:book";
  const supported = support.available && (support.supported_assists.length === 0 || support.supported_assists.includes("book_intent"));
  const disabledReason = !support.available
    ? support.disabled_reason ?? "当前没有可用模型"
    : !supported ? "当前模型没有开放这类辅助" : undefined;
  const hasIntent = Boolean(book.intent?.trim());
  const nextChapter = book.chapters[book.chapters.length - 1] ?? null;

  useEffect(() => {
    if (!touched) setIntent(book.intent ?? "");
  }, [book.intent, touched]);

  function change(value: string) {
    setTouched(true);
    setIntent(value);
    setSaved(false);
    setError(null);
  }

  async function save(): Promise<boolean> {
    if (!intent.trim()) {
      setError("[BOOK_INTENT_REQUIRED] 作品意图：作品意图不能为空。建议：写一句核心处境或起点。");
      return false;
    }
    setSaving(true);
    setError(null);
    try {
      await saveBookIntent(workspaceId, {
        text: intent.trim(),
        expected_plan_revision: planRevision ?? "empty",
        request_key: writingKey("book-intent"),
      });
      setTouched(false);
      setSaved(true);
      await onChanged();
      return true;
    } catch (reason) {
      setError(errorMessage(reason, "作品意图"));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function createFirst() {
    if (saving) return;
    setCreating(true);
    try {
      if (!saved || touched) {
        if (!(await save())) return;
      }
      await onCreateChapter();
    } finally {
      setCreating(false);
    }
  }

  function startAssist() {
    void assistStart(workspaceId, assistKey, {
      kind: "book_intent",
      target: { kind: "book_intent", workspace_id: workspaceId },
      base: { plan_revision: planRevision ?? undefined },
      request_key: writingKey("assist-book"),
    });
  }

  return <article className="plan-view" aria-label="全书构思">
    <header className="plan-view-header">
      <h1><BookOpenText size={20} strokeWidth={1.8} aria-hidden="true" />全书构思</h1>
      <p>全书构思是唯一的写作前提。写清作品从哪里开始，章节建立后就可以直接写正文。</p>
    </header>
    <label className="plan-field">
      <span>作品从什么处境开始</span>
      <textarea
        className="well-input plan-textarea"
        aria-label="作品意图"
        placeholder="例如：暴雨封住了出山的公路，旅馆里少了一名住客。"
        value={intent}
        onChange={event => change(event.target.value)}
      />
      <small>只写现在确定的内容；凶手、结局和章节数都可以暂时未知。</small>
    </label>
    {error && <p className="ui-field-error" role="alert">{error}</p>}
    <div className="plan-actions">
      <Button variant={hasIntent ? "secondary" : "primary"} onClick={() => void save()} busy={saving} disabled={!intent.trim()}>保存作品意图</Button>
      {(saving || saved) && <span className="chapter-editor-status" role="status">{saving ? "保存中…" : "已保存"}</span>}
      <ModelCandidate
        assistKey={assistKey}
        support={{ ...support, available: supported }}
        label="交给模型构思"
        disabledReason={disabledReason}
        onStart={startAssist}
        onAdopt={text => change(text)}
      />
    </div>

    <section className="plan-next-step" aria-label="下一步">
      <div>
        <strong>{!hasIntent ? "先写一句作品起点" : nextChapter ? "继续写正文" : "建立第一章"}</strong>
        <p>{!hasIntent
          ? "保存全书构思之前不能建立章节；写完这一句就可以直接开写。"
          : nextChapter
          ? `第${nextChapter.order + 1}章的稿纸已经可以写；新建章节会直接带上空白正文。`
          : "建立第一章后直接进入稿纸，没有章节构思或确认步骤。"}</p>
      </div>
      {hasIntent && nextChapter
        ? <Button variant="primary" onClick={() => onSelectChapter(nextChapter.id)}>
          继续写第{nextChapter.order + 1}章<ArrowRight size={15} aria-hidden="true" />
        </Button>
        : hasIntent ? <Button variant="primary" onClick={() => void createFirst()} busy={creating} disabled={saving || !intent.trim()}>
          <PenLine size={16} strokeWidth={1.8} aria-hidden="true" />建立第一章
        </Button> : null}
    </section>

    <section className="plan-section">
      <h2>信息线</h2>
      {book.information_threads.length === 0
        ? <div className="plan-empty"><p>信息线从正文里长出来：保存正文后，索引给出候选，确认后才会出现在这里。</p></div>
        : <ul className="plan-thread-list">
          {book.information_threads.map(thread => <li key={thread.id}>{thread.summary}</li>)}
        </ul>}
    </section>

    <section className="plan-section">
      <h2>章节</h2>
      {book.chapters.length === 0
        ? <div className="plan-empty">
          <p>建立章节后会直接得到空白稿纸，可以自己写，也可以让模型起草。</p>
        </div>
        : <ul className="plan-chapter-list">
          {book.chapters.map(chapter => <li key={chapter.id}>
            <button className="plan-chapter-row" onClick={() => onSelectChapter(chapter.id)}>
              <span className="writing-nav-order">第{chapter.order + 1}章</span>
              <span className="writing-nav-title">{chapter.title || chapter.summary || "未命名"}</span>
              <span className="writing-nav-order">{chapter.prose_path ? "正文" : "缺正文"}</span>
            </button>
          </li>)}
        </ul>}
    </section>
  </article>;
}
