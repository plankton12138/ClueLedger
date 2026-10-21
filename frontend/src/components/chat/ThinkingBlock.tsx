/**
 * 思考块（V05-2C）：默认折叠，运行中也不自动展开。
 *
 * 用原生 `<details>`，不控制 `open`：作者展开后继续有新内容进来时，浏览器保留他的
 * 选择；切到别的会话再回来时重新挂载，自然回到折叠的默认状态。
 *
 * 只展示供应商实际返回的思考文本。没有文本就不渲染这个块——不额外调用模型编一段
 * 「思考过程」，也不把签名或加密字段当内容显示。
 */

import { formatDuration, type ChatBlock } from "../../state/chatMessages";
import ChatMarkdown from "./ChatMarkdown";

interface Props {
  block: ChatBlock;
}

export default function ThinkingBlock({ block }: Props) {
  const streaming = block.completion === "streaming";
  const duration = formatDuration(block.durationMs);
  // 「思考输出持续时间」：应用只观察到输出了多久，不代表模型内部思考的全部时间。
  const summary = streaming ? "思考中…" : duration ? `思考 · 输出 ${duration}` : "思考";

  return (
    <details className="chat-thinking">
      <summary className="chat-thinking-head">{summary}</summary>
      <div className="chat-thinking-body">
        <ChatMarkdown text={block.text} />
      </div>
    </details>
  );
}
