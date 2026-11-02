/**
 * 全书构思（1.0.9 P4）。
 *
 * 空白作品从这里开始：写一句起点，建立第一章，再进入章节构思。模型只提供候选文字，
 * 采用后仍由作者保存，保存成功才创建正式构思格式。
 */

import { useEffect, useState } from "react";
import { ArrowRight, BookOpenText, Check, Circle, PenLine } from "lucide-react";

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
  const hasChapters = book.chapters.length > 0;
  const hasProse = book.chapters.some(chapter => Boolean(chapter.prose_path));
  const nextChapter = book.chapters.find(chapter => !chapter.prose_path) ?? book.chapters[book.chapters.length - 1] ?? null;
  const currentStep = !hasIntent ? 0 : !hasProse ? 1 : 2;

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
      <p>这里不需要完整大纲。先确定作品从哪里开始，再为第一章写一句可执行的构思。</p>
    </header>
    <ol className="plan-progress" aria-label="开始写作的三个步骤">
      {[{
        title: "作品起点",
        note: "一句核心处境",
      }, {
        title: "章节构思",
        note: "本章改变什么",
      }, {
        title: "开始正文",
        note: "手写或模型起草",
      }].map((step, index) => <li key={step.title} data-state={index < currentStep ? "done" : index === currentStep ? "current" : "later"}>
        <span className="plan-progress-mark" aria-hidden="true">{index < currentStep ? <Check size={13} /> : <Circle size={11} fill="currentColor" />}</span>
        <span><strong>{step.title}</strong><small>{step.note}</small></span>
      </li>)}
    </ol>
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

    {!hasProse && <section className="plan-next-step" aria-label="下一步">
      <div>
        <strong>{!hasIntent ? "第一步：先完成全书构思" : hasChapters ? "下一步：完成章节构思" : "下一步：建立第一章"}</strong>
        <p>{!hasIntent
          ? "先写并保存一句作品起点。完成这一步前不能建立章节，也不会出现正文入口。"
          : hasChapters
          ? "先为第一章确定“发生什么变化”和“读者或角色知道什么”，确认后才会打开正文。"
          : "建立章节后，只需再写一句本章构思，不用先规划整本书。"}</p>
      </div>
      {hasIntent && hasChapters && nextChapter
        ? <Button variant="primary" onClick={() => onSelectChapter(nextChapter.id)}>
          继续构思第{nextChapter.order + 1}章<ArrowRight size={15} aria-hidden="true" />
        </Button>
        : hasIntent ? <Button variant="primary" onClick={() => void createFirst()} busy={creating} disabled={saving || !intent.trim()}>
          <PenLine size={16} strokeWidth={1.8} aria-hidden="true" />建立第一章
        </Button> : null}
    </section>}

    {book.information_threads.length > 0 && <section className="plan-section">
      <h2>信息线</h2>
      <ul className="plan-thread-list">
        {book.information_threads.map(thread => <li key={thread.id}>{thread.summary}</li>)}
      </ul>
    </section>}

    <section className="plan-section">
      <h2>章节</h2>
      {book.chapters.length === 0
        ? <div className="plan-empty">
          <p>建立第一章后，它会先进入章节构思，不会直接生成空白正文。</p>
        </div>
        : <ul className="plan-chapter-list">
          {book.chapters.map(chapter => <li key={chapter.id}>
            <button className="plan-chapter-row" onClick={() => onSelectChapter(chapter.id)}>
              <span className="writing-nav-order">第{chapter.order + 1}章</span>
              <span className="writing-nav-title">{chapter.title || chapter.summary || "尚未构思"}</span>
              <span className="writing-nav-order">{chapter.prose_path ? "已开始" : chapter.confirmed ? "已确认" : "待确认"}</span>
            </button>
          </li>)}
        </ul>}
    </section>
  </article>;
}
