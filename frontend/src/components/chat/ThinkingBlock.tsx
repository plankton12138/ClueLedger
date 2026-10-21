/**
 * 思考块（V05-2C）：默认折叠，运行中也不自动展开。
 *
 * 折叠时不挂载正文；展开后的实时思考保留原文换行，结束后才解析 Markdown，
 * 避免每个新片段都重新解析长文本。原生 details 保留作者的展开选择。
 *
 * 只展示供应商实际返回的思考文本。没有文本就不渲染这个块——不额外调用模型编一段
 * 「思考过程」，也不把签名或加密字段当内容显示。
 */

import { memo, useState } from "react";

import { formatDuration, type ChatBlock } from "../../state/chatMessages";
import ChatMarkdown from "./ChatMarkdown";

interface Props {
  block: ChatBlock;
}

function ThinkingBlock({ block }: Props) {
  const [open, setOpen] = useState(false);
  const streaming = block.completion === "streaming";
  const duration = formatDuration(block.durationMs);
  // 「思考输出持续时间」：应用只观察到输出了多久，不代表模型内部思考的全部时间。
  const summary = streaming ? "思考中…" : duration ? `思考 · 输出 ${duration}` : "思考";

  return (
    <details className="chat-thinking" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="chat-thinking-head">{summary}</summary>
      {open && (
        <div className="chat-thinking-body">
          {streaming ? (
            <div className="chat-thinking-text">{block.text}</div>
          ) : (
            <ChatMarkdown text={block.text} />
          )}
        </div>
      )}
    </details>
  );
}

export default memo(ThinkingBlock);
