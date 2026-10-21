/**
 * 一条模型回复（V05-2C）。
 *
 * 公开文字与思考按模型实际输出顺序排列，思考默认折叠。回复不完整时在正文下方标出
 * 原因（停止、截断、失败、中断），不能因为「有一段文字」就显示成完整成功。
 */

import { orderedBlocks, type ChatBlock, type ChatMessage } from "../../state/chatMessages";
import ChatMarkdown from "./ChatMarkdown";
import MessageActions from "./MessageActions";
import ThinkingBlock from "./ThinkingBlock";

const COMPLETION_NOTE: Record<string, string> = {
  stopped: "已停止，回复未完整生成。",
  truncated: "回复达到输出上限，后面的内容没有生成。",
  failed: "生成中断，回复未完整。",
  interrupted: "记录中断，这段回复可能不完整。",
};

interface Props {
  message: ChatMessage;
  wide?: boolean;
  /** 宽阅读视图里不重复放操作按钮。 */
  showActions?: boolean;
  onExpand?: () => void;
  onCopyFailed?: (message: ChatMessage) => void;
}

function Block({ block }: { block: ChatBlock }) {
  if (block.kind === "thinking") return <ThinkingBlock block={block} />;
  if (!block.text.trim()) return null;
  return <ChatMarkdown text={block.text} />;
}

export default function AssistantMessageView({
  message,
  wide = false,
  showActions = true,
  onExpand,
  onCopyFailed,
}: Props) {
  const blocks = orderedBlocks(message);
  const note = COMPLETION_NOTE[message.completion];

  // 还没有任何可读内容时不占版面：等待阶段由状态行说明，不画一个空气泡。
  // （只有工具调用的响应同理——工具过程自己就是可见内容。）
  if (blocks.length === 0 && !message.text.trim()) return null;

  // 只有思考、没有公开文字的响应是过程，不是一条回复：不加气泡边框，
  // 免得会话里出现一串空壳。
  const hasReply = blocks.some((block) => block.kind === "text" && block.text.trim());
  const classes = ["chat-reply"];
  if (wide) classes.push("chat-reply-wide");
  if (!hasReply) classes.push("chat-reply-plain");

  return (
    <div className="chat-msg">
      <div className={classes.join(" ")}>
        {blocks.map((block) => (
          <Block key={block.id} block={block} />
        ))}
        {note && <p className="chat-note">{note}</p>}
      </div>
      {showActions && (
        <MessageActions
          message={message}
          onExpand={onExpand ?? (() => undefined)}
          onCopyFailed={() => onCopyFailed?.(message)}
        />
      )}
    </div>
  );
}
