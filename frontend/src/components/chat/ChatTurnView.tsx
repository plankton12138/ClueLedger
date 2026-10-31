/**
 * 一轮对话（V05-2B）。
 *
 * 阅读顺序就是执行顺序：作者要求、模型内容、工具操作、后续回复、本轮结果。
 * 组件按轮次记忆化——只有正在变化的那一轮会重绘，已完成的历史轮次保持原样。
 */

import { memo } from "react";

import type { RunPhase } from "../../api/workspace";
import { hasLaterStorySubmission, hasSavedStoryChanges, phaseLabel, type ChatMessage, type ChatTurn } from "../../state/chatMessages";
import AssistantMessageView from "./AssistantMessageView";
import AuthorQuestionCard from "./AuthorQuestionCard";
import ModuleChangeCard from "./ModuleChangeCard";
import ReviewReportView from "./ReviewReport";
import TaskCard from "./TaskCard";
import ToolActivity from "./ToolActivity";
import TurnContextSummary from "./TurnContextSummary";
import TurnIdeas from "./TurnIdeas";
import TurnMaterials from "./TurnMaterials";
import RevertConfirmation from "./RevertConfirmation";

const STATUS_LABEL: Record<string, string> = {
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  stopped: "已停止",
  interrupted: "中断",
  needs_input: "等待你回答",
};

const RECORDING_LABEL: Record<string, string> = {
  committed: "已记录",
  no_change: "没有改动",
  unrecorded: "历史未记录",
};

interface Props {
  turn: ChatTurn;
  /** 这一轮在会话中的位置（从 1 开始）：选段记成灵感时作为来源定位。 */
  turnIndex: number;
  /** 是否是当前正在进行的这一轮。 */
  running: boolean;
  phase: RunPhase | null;
  onExpand: (message: ChatMessage) => void;
  onCopyFailed: (message: ChatMessage) => void;
  onRevert: (commit: string) => void | Promise<void>;
  onRecord: () => void;
  /** 对本轮改动发起一次只读检查；没有可检查的文件时为 null。 */
  onReview: (() => void) | null;
  /** 模块变更结果与撤销需要定位作品与会话；缺失时只展示事实。 */
  workspaceId: string | null;
  conversationId: string | null;
}

function ChatTurnView({
  turn,
  turnIndex,
  running,
  phase,
  onExpand,
  onCopyFailed,
  onRevert,
  onRecord,
  onReview,
  workspaceId,
  conversationId,
}: Props) {
  const result = turn.result;

  return (
    <section className="chat-turn" aria-label="一轮对话">
      <div className="chat-msg mine" data-role="user" data-turn-id={turn.id} data-turn-index={turnIndex}>
        <div className="chat-bubble">{turn.prompt}</div>
      </div>

      <TurnMaterials materials={turn.materials} />
      <TurnIdeas ideas={turn.ideas} />
      {turn.injection && <TurnContextSummary injection={turn.injection} />}
      <TaskCard turn={turn} />
      <ModuleChangeCard turn={turn} workspaceId={workspaceId} conversationId={conversationId} />

      {turn.items.map((item, index) =>
        item.kind === "message" ? (
          <AssistantMessageView
            key={item.key}
            message={item.message}
            turnId={turn.id}
            turnIndex={turnIndex}
            onExpand={onExpand}
            onCopyFailed={onCopyFailed}
          />
        ) : (
          <ToolActivity key={item.key} tool={item.tool} submittedLater={hasLaterStorySubmission(turn, index)} />
        ),
      )}

      {turn.incomplete && (
        <p className="chat-note">这段记录不完整：部分内容没有保存下来，以上的文字就是全部。</p>
      )}

      <AuthorQuestionCard turn={turn} running={running} />

      {turn.errors.map((error) => (
        <div key={error.key} className="chat-error">
          {error.message}
        </div>
      ))}

      {running && (
        <div className="chat-status" role="status" aria-live="polite">
          {phaseLabel(phase)}…
        </div>
      )}

      {result && (
        <div className={`chat-card ${result.status === "completed" ? "result" : "warn"}`} data-testid="turn-result" data-status={result.status}>
          <div className="flex items-center gap-2">
            <span className="font-bold">{STATUS_LABEL[result.status] ?? result.status}</span>
            <span className="text-sub">{result.recording === "no_change" && hasSavedStoryChanges(turn)
              ? "构思已保存" : RECORDING_LABEL[result.recording] ?? result.recording}</span>
            {result.commit && (
              <code className="text-[10px] text-mute">{result.commit.slice(0, 7)}</code>
            )}
          </div>
          {result.changed_paths.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-sub">
              {result.changed_paths.map((path) => (
                <li key={path}>· {path}</li>
              ))}
            </ul>
          )}
          {result.note && <p className="mt-1 text-amber">{result.note}</p>}
          <div className="mt-2 flex gap-2">
            {result.recording === "committed" && result.commit && (
              <RevertConfirmation commit={result.commit} paths={result.changed_paths} onConfirm={onRevert} />
            )}
            {result.recording === "unrecorded" && (
              <button
                type="button"
                className="btn btn-gray px-2 py-0.5 text-[10px]"
                onClick={onRecord}
              >
                补记到历史
              </button>
            )}
            {onReview && !turn.review && (
              <button
                type="button"
                className="btn btn-gray px-2 py-0.5 text-[10px]"
                onClick={onReview}
              >
                检查本次改动
              </button>
            )}
          </div>
        </div>
      )}

      {turn.review && <ReviewReportView review={turn.review} />}
    </section>
  );
}

export default memo(ChatTurnView);
