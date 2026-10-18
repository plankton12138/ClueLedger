/**
 * 任务卡片（§3.3）：任务建议 / 执行任务 / 待审阅候选 / 已接受结果。
 *
 * 硬约束（四透镜②）：
 * - 卡片只渲染受限结构化 payload，不从回复文本解析按钮或链接；
 * - 「开始」在范围齐全（missing 为空）时才可用，缺项给出具体解决入口；
 * - 候选接受走现有接受/拒绝规则，聊天文本不代替审批；
 * - 结果卡以 Commit 回读为准，不显示未经确认的「已生效」。
 */

import { useState } from "react";
import type {
  CandidatePayload,
  ChatMessage,
  ResultPayload,
  TaskDraftPayload,
  TaskExecutionPayload,
} from "../../api/chat";
import { taskKindLabels } from "../../api/chat";
import { Pill } from "../Workbench";
import { useV2Store } from "../../state/v2";
import { titleOf } from "../Workbench";

const NEW_CHAPTER = "__new_chapter__";

const executionLabels: Record<TaskExecutionPayload["state"], string> = {
  queued: "排队中",
  running: "执行中",
  waiting: "等待审阅",
  completed: "已完成",
  cancelled: "已取消",
  failed: "执行失败",
};

export function TaskDraftCard({
  message,
  abandoned,
  onAdjust,
  onStart,
  onAbandon,
}: {
  message: ChatMessage;
  abandoned: boolean;
  onAdjust(
    draftId: string,
    patch: { instruction?: string; goal?: string; target_ids?: string[] },
  ): void;
  onStart(draftId: string): void;
  onAbandon(draftId: string): void;
}) {
  const draft = message.payload as TaskDraftPayload;
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState(draft.instruction);
  const [goal, setGoal] = useState(draft.goal);
  const [targets, setTargets] = useState(draft.target_ids);
  const artifacts = useV2Store((s) => s.storyArtifacts);
  const chapters = useV2Store((s) => s.chapters);
  const [newChapterId] = useState(() => `ch_${crypto.randomUUID().slice(0, 12)}`);
  const options = [
    ...artifacts.map((a) => ({ id: a.artifact_id, title: titleOf(a) })),
    ...chapters.map((c) => ({ id: c.chapter_id, title: c.title })),
  ];
  const draftChapter = chapters.find((c) => c.chapter_id === draft.goal);
  const goalLabel = draftChapter ? `改写「${draftChapter.title}」` : "新章（追加到末尾）";
  const started = draft.job_ref !== null;
  if (abandoned) {
    return (
      <article className="chat-card chat-card-draft" aria-label="任务建议（已放弃）">
        <header className="chat-card-head">
          <b>任务建议 · 已放弃</b>
          <Pill tone="blue">{taskKindLabels[draft.task_kind]}</Pill>
        </header>
        <p className="chat-card-note">这份建议已放弃，没有创建任何创作任务。</p>
      </article>
    );
  }
  return (
    <article className="chat-card chat-card-draft" aria-label="任务建议">
      <header className="chat-card-head">
        <b>任务建议</b>
        <Pill tone="blue">{taskKindLabels[draft.task_kind]}</Pill>
      </header>
      <dl className="chat-card-grid">
        <dt>目标</dt>
        <dd>{draft.task_kind === "chapter_draft" ? goalLabel : draft.goal}</dd>
        <dt>范围</dt>
        <dd>
          {draft.task_kind === "chapter_draft"
            ? "账本与作者要求（不要求节拍）"
            : draft.target_ids.length > 0
              ? draft.target_ids.join("、")
              : draft.task_kind === "check"
                ? "当前项目已保存内容"
                : "未指定（开始前需补齐）"}
        </dd>
        <dt>基础版本</dt>
        <dd>{draft.base_version ? draft.base_version.slice(0, 12) : "未绑定"}</dd>
        <dt>依据</dt>
        <dd>{draft.basis.join("；")}</dd>
      </dl>
      {draft.missing.length > 0 && (
        <ul className="chat-card-missing" aria-label="缺失项">
          {draft.missing.map((m, i) => (
            <li key={typeof m === "string" ? m : `${m.code}:${i}`}>
              {typeof m === "string" ? m : `${m.why}；${m.fix}`}
            </li>
          ))}
        </ul>
      )}
      {editing ? (
        <form
          className="chat-card-form"
          onSubmit={(e) => {
            e.preventDefault();
            onAdjust(draft.draft_id, { instruction, goal, target_ids: targets });
            setEditing(false);
          }}
        >
          <label className="field">
            任务说明
            <textarea
              className="well-input min-h-20 resize-y"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              aria-label="任务说明"
            />
          </label>
          {draft.task_kind === "chapter_draft" ? (
            <label className="field">
              写哪一章
              <select
                className="well-input"
                aria-label="目标章节"
                value={chapters.some((c) => c.chapter_id === goal) ? goal : NEW_CHAPTER}
                onChange={(e) =>
                  setGoal(e.target.value === NEW_CHAPTER ? newChapterId : e.target.value)
                }
              >
                <option value={NEW_CHAPTER}>新章（追加到末尾）</option>
                {chapters.map((c) => (
                  <option key={c.chapter_id} value={c.chapter_id}>
                    改写：{c.title}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <fieldset className="field">
                <legend>任务范围</legend>
                {options.map((option) => <label key={option.id} className="flex gap-2 text-xs">
                  <input type="checkbox" checked={targets.includes(option.id)}
                    onChange={(e) => setTargets(e.target.checked
                      ? [...targets, option.id] : targets.filter((id) => id !== option.id))} />
                  {option.title}
                </label>)}
                {options.length === 0 && <span>暂无可选对象，请先规划并接受故事内容。</span>}
              </fieldset>
              <label className="field">
                目标
                <input
                  className="well-input"
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  aria-label="任务目标"
                />
              </label>
            </>
          )}
          <div className="flex gap-2">
            <button className="btn px-3 py-1.5 text-xs" type="submit">
              保存修改
            </button>
            <button
              type="button"
              className="btn btn-gray px-3 py-1.5 text-xs"
              onClick={() => setEditing(false)}
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <div className="chat-card-actions">
          {started ? (
            <span className="chat-card-note">已开始，执行进度见下方任务卡。</span>
          ) : (
            <>
              <button
                className="btn px-3 py-1.5 text-xs"
                disabled={draft.missing.length > 0}
                onClick={() => onStart(draft.draft_id)}
              >
                开始（创建创作任务）
              </button>
              <button
                className="btn btn-gray px-3 py-1.5 text-xs"
                onClick={() => setEditing(true)}
              >
                调整
              </button>
              <button
                className="btn btn-gray px-3 py-1.5 text-xs"
                onClick={() => onAbandon(draft.draft_id)}
              >
                放弃
              </button>
            </>
          )}
        </div>
      )}
      {draft.missing.length > 0 && !started && (
        <p className="chat-card-note">
          建议本身不会创建任务；补齐缺失项后「开始」才可用。
        </p>
      )}
    </article>
  );
}

export function TaskExecutionCard({
  message,
  onView,
  onCancel,
  onRetry,
  onOpenChange,
}: {
  message: ChatMessage;
  onView(candidateMessageId: string): void;
  onCancel(jobRef: string): void;
  onRetry(jobRef: string): void;
  onOpenChange(changeId: string): void;
}) {
  const execution = message.payload as TaskExecutionPayload;
  const tone =
    execution.state === "failed"
      ? "red"
      : execution.state === "cancelled"
        ? "amber"
        : execution.state === "waiting"
          ? "green"
          : "blue";
  return (
    <article className="chat-card chat-card-execution" aria-label="执行任务">
      <header className="chat-card-head">
        <b>创作任务 · {taskKindLabels[execution.task_kind]}</b>
        <Pill tone={tone}>{executionLabels[execution.state]}</Pill>
      </header>
      <dl className="chat-card-grid">
        <dt>阶段</dt>
        <dd>{execution.stage}</dd>
        <dt>实际模型</dt>
        <dd>{execution.model || "未记录"}</dd>
        <dt>任务引用</dt>
        <dd className="break-all">{execution.job_ref}</dd>
      </dl>
      {execution.error && (
        <p className="chat-card-error">
          {execution.error.why}（怎样算对：{execution.error.fix}）
        </p>
      )}
      <div className="chat-card-actions">
        {execution.change_ref && <button className="btn px-3 py-1.5 text-xs"
          onClick={() => onOpenChange(execution.change_ref!)}>打开变更流程</button>}
        {execution.state === "waiting" && execution.candidate_message_id && (
          <button
            className="btn px-3 py-1.5 text-xs"
            onClick={() => onView(execution.candidate_message_id!)}
          >
            查看候选
          </button>
        )}
        {execution.state === "failed" && (
          <button
            className="btn btn-gray px-3 py-1.5 text-xs"
            onClick={() => onRetry(execution.job_ref)}
          >
            重试任务
          </button>
        )}
        {!execution.change_ref && !["completed", "cancelled", "failed"].includes(execution.state) && (
          <button
            className="btn btn-gray px-3 py-1.5 text-xs"
            onClick={() => onCancel(execution.job_ref)}
          >
            取消任务
          </button>
        )}
      </div>
    </article>
  );
}

export function CandidateCard({
  message,
  onReview,
  onAccept,
}: {
  message: ChatMessage;
  onReview(): void;
  onAccept(messageId: string): void;
}) {
  const candidate = message.payload as CandidatePayload;
  return (
    <article className="chat-card chat-card-candidate" aria-label="待审阅候选">
      <header className="chat-card-head">
        <b>待审阅候选</b>
        <Pill tone={candidate.accepted ? "green" : "amber"}>
          {candidate.accepted ? "已接受" : "等待作者审阅"}
        </Pill>
      </header>
      <p className="chat-card-note">{candidate.summary}</p>
      <p className="chat-card-note">
        任务完成不等于内容已保存；接受后才会生成正式版本（Commit）。
      </p>
      <div className="chat-card-actions">
        <button className="btn btn-gray px-3 py-1.5 text-xs" onClick={onReview}>
          打开审阅
        </button>
        {!candidate.accepted && (
          <button
            className="btn px-3 py-1.5 text-xs"
            onClick={() => onAccept(message.message_id)}
          >
            接受候选并提交
          </button>
        )}
      </div>
    </article>
  );
}

export function ResultCard({ message }: { message: ChatMessage }) {
  const result = message.payload as ResultPayload;
  return (
    <article className="chat-card chat-card-result" aria-label="已接受结果">
      <header className="chat-card-head">
        <b>已接受 · 正式版本</b>
        <Pill tone="green">已提交</Pill>
      </header>
      <dl className="chat-card-grid">
        <dt>正式提交</dt>
        <dd className="break-all">{result.commit_id}</dd>
        <dt>摘要</dt>
        <dd>{result.summary}</dd>
        <dt>影响对象</dt>
        <dd>{result.target_ids.join("、") || "—"}</dd>
      </dl>
      <p className="chat-card-note">
        以 Commit 回读为准；正文、账本与修订页面查看正式内容。
      </p>
    </article>
  );
}
