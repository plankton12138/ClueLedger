/**
 * 一轮里的作者提问（0.8）。
 *
 * 问题由服务端持久化：这里只显示事实、收集回答，并把 `reply_to_question` 交给同一套
 * 对话提交接口。是否已回答以事件为准，重复点击由服务端拒绝，不在前端猜结果。
 */

import { useState } from "react";

import { EMPTY_CONTEXT } from "../../api/workspace";
import type { ChatTurn } from "../../state/chatMessages";
import { useConversationsStore } from "../../state/conversations";

interface Props {
  turn: ChatTurn;
  /** 这一轮仍在执行：执行结束前不接受回答。 */
  running: boolean;
}

export default function AuthorQuestionCard({ turn, running }: Props) {
  const send = useConversationsStore((state) => state.send);
  const pendingKey = useConversationsStore((state) => state.pendingKey);
  const [draft, setDraft] = useState("");
  const question = turn.question;

  if (!question) return null;
  const disabled = running || pendingKey !== null || question.answer !== "";

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    // 回答不改变作者当前草稿：显式给空的随行引用，避免把草稿里的灵感带进这一轮。
    void send(trimmed, EMPTY_CONTEXT, {
      purpose: "chat",
      ideas: [],
      replyToQuestion: question.questionId,
    });
  };

  return (
    <div
      className="mt-1 rounded border border-line bg-surface px-2 py-1.5 text-[11px]"
      data-testid="author-question"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-bold">{question.answer ? "已收到你的回答" : "需要你决定"}</span>
        {question.createdAt && <span className="text-mute">{question.createdAt}</span>}
      </div>
      <p className="mt-1 whitespace-pre-wrap">{question.question}</p>
      {question.related.length > 0 && (
        <p className="mt-1 text-mute">相关：{question.related.join("、")}</p>
      )}

      {question.answer ? (
        <p className="mt-1 text-sub">你的回答：{question.answer}</p>
      ) : (
        <>
          {question.options.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {question.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="btn btn-gray px-2 py-0.5 text-[10px]"
                  disabled={disabled}
                  onClick={() => submit(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          <div className="mt-1 flex gap-1">
            <input
              className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-[11px]"
              aria-label="回答作者问题"
              placeholder="也可以直接写下你的决定"
              value={draft}
              disabled={disabled}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submit(draft);
                }
              }}
            />
            <button
              type="button"
              className="btn btn-gray px-2 py-0.5 text-[10px]"
              disabled={disabled || draft.trim() === ""}
              onClick={() => submit(draft)}
            >
              回答
            </button>
          </div>
        </>
      )}

      {running && !question.answer && (
        <p className="mt-1 text-mute">这一轮仍在执行；结束后可以回答。</p>
      )}
    </div>
  );
}
