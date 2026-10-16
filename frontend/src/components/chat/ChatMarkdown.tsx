/**
 * 安全 Markdown 渲染（§3.2）：允许代码、列表与引用，禁用模型输出的原始 HTML。
 *
 * react-markdown 默认不解析原始 HTML（不引入 rehype-raw），模型回复里的
 * <script>/<img onerror> 等一律按文本展示；链接只渲染为普通文本节点，
 * 任何「任务按钮」都由结构化 payload 渲染，不来自回复中的链接。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ChatMarkdown({ text }: { text: string }) {
  return (
    <div className="chat-markdown text-[13px] leading-6">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children }) => <span className="text-blue underline">{children}</span>,
          code: ({ className, children }) =>
            className ? (
              <pre className="my-2 overflow-x-auto rounded border border-line bg-white/70 p-2 text-[12px]">
                <code>{children}</code>
              </pre>
            ) : (
              <code className="rounded border border-line bg-white/70 px-1 text-[12px]">
                {children}
              </code>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
