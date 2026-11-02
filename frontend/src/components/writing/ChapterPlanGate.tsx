/**
 * 章节前置区（1.0.9 P4；引导按原型简化）。
 *
 * 一章开始前只问一件事：这一章要改变什么。作者写一句话并「整理构思」，服务端保存
 * 后整理出本章摘要与信息变化；作者在确认卡片里核对，再「开始写作」。模型只给候选，
 * 不自动开始。已开始的章节修改构思后，同一个开始按钮用现有正文摘要重新确认。
 */

import { useEffect, useRef, useState } from "react";
import { ArrowRight } from "lucide-react";

import { errorMessage } from "../../api/errors";
import {
  saveChapterPlan,
  startChapter,
  writingKey,
  type ChapterView,
  type ModelSupportView,
} from "../../api/writing";
import { useWritingStore } from "../../state/writing";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import ModelCandidate from "./ModelCandidate";

export default function ChapterPlanGate({
  workspaceId,
  chapter,
  planRevision,
  support,
  onChanged,
  onCancel,
  onStarted,
}: {
  workspaceId: string;
  chapter: ChapterView;
  planRevision: string | null;
  support: ModelSupportView;
  onChanged: () => Promise<void>;
  onCancel?: () => void;
  onStarted?: () => void;
}) {
  const plan = chapter.plan;
  const prose = chapter.prose;
  const [intent, setIntent] = useState(plan?.intent ?? "");
  const [touched, setTouched] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const assistStart = useWritingStore(state => state.assistStart);
  const assistKey = plan ? `chapter_plan:${plan.id}` : "chapter_plan:missing";
  const supported = support.available && (support.supported_assists.length === 0 || support.supported_assists.includes("chapter_plan"));
  const disabledReason = !support.available
    ? support.disabled_reason ?? "当前没有可用模型"
    : !supported ? "当前模型没有开放这类辅助" : undefined;
  const stale = Boolean(prose && prose.blocked_reason === "chapter_plan_stale");
  // 句子与已保存的构思一致时进入核对；一旦继续输入就回到整理状态。
  const settled = Boolean(plan) && intent.trim() !== "" && intent.trim() === plan!.intent;

  useEffect(() => {
    if (!touched) setIntent(plan?.intent ?? "");
  }, [plan?.intent, touched]);

  function change(value: string) {
    setTouched(true);
    setAdjusting(false);
    setIntent(value);
    setError(null);
  }

  async function savePlan(): Promise<{ plan_revision: string; chapter_revision: string } | null> {
    if (!plan) {
      setError("[CHAPTER_PLAN_REQUIRED] 章节构思：章节构思不存在。建议：刷新工作台后重新选择章节。");
      return null;
    }
    if (!intent.trim()) {
      setError("[CHAPTER_PLAN_REQUIRED] 章节构思：还没有说明本章要改变什么。建议：写一句本章变化，或选择「交给模型构思」。");
      return null;
    }
    const result = await saveChapterPlan(workspaceId, plan.id, {
      intent: intent.trim(),
      expected_chapter_revision: plan.revision,
      expected_plan_revision: planRevision ?? plan.revision,
      request_key: writingKey(`plan-${plan.id}`),
    });
    setTouched(false);
    return { plan_revision: result.plan_revision, chapter_revision: result.chapter_revision };
  }

  async function compose() {
    setSaving(true);
    setError(null);
    try {
      // 句子没有变化时不需要再提交一次；只是从调整回到核对。
      if (plan && intent.trim() === plan.intent) {
        setAdjusting(false);
        return;
      }
      const result = await savePlan();
      if (result) {
        setAdjusting(false);
        await onChanged();
      }
    } catch (reason) {
      setError(errorMessage(reason, "章节构思"));
    } finally {
      setSaving(false);
    }
  }

  async function start() {
    if (!plan) return;
    setStarting(true);
    setError(null);
    try {
      // 核对阶段句子与已保存构思一致，直接用当前修订开始；不再重复提交。
      await startChapter(workspaceId, plan.id, {
        expected_story_revision: planRevision ?? plan.revision,
        expected_chapter_revision: plan.revision,
        attach_path: prose?.path ?? null,
        attach_digest: prose?.digest ?? null,
        request_key: writingKey(`start-${plan.id}`),
      });
      await onChanged();
      onStarted?.();
    } catch (reason) {
      setError(errorMessage(reason, "开始写作"));
      void onChanged();
    } finally {
      setStarting(false);
    }
  }

  function startAssist() {
    if (!plan) return;
    void assistStart(workspaceId, assistKey, {
      kind: "chapter_plan",
      target: { kind: "chapter_plan", workspace_id: workspaceId, chapter_id: plan.id },
      base: { plan_revision: planRevision ?? undefined, chapter_revision: plan.revision },
      request_key: writingKey(`assist-plan-${plan.id}`),
    });
  }

  if (!plan) return <StatusMessage tone="error">这一章的构思不存在。刷新工作台后重新选择章节。</StatusMessage>;

  const startLabel = prose ? (stale ? "保存并重新确认" : "保存并继续写") : "开始写作";
  return <article className="plan-view" aria-label="章节构思">
    <header className="plan-view-header">
      <h1>第{plan.order + 1}章{plan.title ? ` · ${plan.title}` : ""}</h1>
      <p>{prose ? "修改构思不会静默改写正文；重新确认后再继续写。" : "正文还没有创建。用一句话说明这一章会改变什么，未知的部分可以继续未知。"}</p>
    </header>
    <label className="plan-field">
      <span>这一章要改变什么？</span>
      <textarea
        ref={field}
        className="well-input plan-textarea"
        aria-label="章节构思"
        placeholder="例如：主角发现死者房间里的钥匙不见了，开始怀疑管家。"
        value={intent}
        onChange={event => change(event.target.value)}
      />
    </label>
    {error && <p className="ui-field-error" role="alert">{error}</p>}
    {settled && !adjusting ? <section className="plan-review" aria-label="本章构思">
      <strong>本章构思</strong>
      <p>{plan.summary || plan.intent}</p>
      {plan.moves.length > 0 && <ul className="plan-move-list">
        {plan.moves.map(move => <li key={move.id}>
          <span>{move.statement}</span>
          {move.thread_summary && <small>{move.thread_summary}</small>}
        </li>)}
      </ul>}
      {stale && <p className="ui-field-hint">本章构思已变化，正文暂时锁定；开始写作会重新确认。</p>}
      <div className="plan-review-actions">
        <Button variant="quiet" onClick={() => { setAdjusting(true); field.current?.focus(); }}>继续调整</Button>
        <Button variant="primary" onClick={() => void start()} busy={starting}>
          <ArrowRight size={16} strokeWidth={2} aria-hidden="true" />{startLabel}
        </Button>
      </div>
    </section> : <div className="plan-actions">
      <Button variant="primary" onClick={() => void compose()} busy={saving} disabled={!intent.trim()}>
        整理构思
      </Button>
      <ModelCandidate
        assistKey={assistKey}
        support={{ ...support, available: supported }}
        label="交给模型构思"
        disabledReason={disabledReason}
        onStart={startAssist}
        onAdopt={text => change(text)}
      />
      {onCancel && <Button variant="quiet" onClick={onCancel}>返回正文</Button>}
    </div>}
  </article>;
}
