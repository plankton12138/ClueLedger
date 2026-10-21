/**
 * 对话栏的 Markdown 渲染（V05-2C）。
 *
 * 公开回复按 Markdown 阅读，但对话栏不是稿纸：不缩进首行、不假设长文，宽表与代码块
 * 可以横向滚动。沿用稿纸同一套渲染器与语法扩展，避免正文与回复出现两种排版规则。
 *
 * 三条边界：
 * - 内嵌 HTML 不执行（`skipHtml`），链接只放行 http/https/mailto；
 * - 外部图片不自动加载，保留替代文字，作者需要时自己点链接；
 * - 任务列表只读：勾选不写回，回复内容是记录，不是表单。
 */

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTS: Components = {
  a({ href, children }) {
    if (!href) return <span>{children}</span>;
    return (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  },
  img({ alt }) {
    return <span className="chat-image-alt">［图片：{alt?.trim() || "无描述"}］</span>;
  },
  input({ checked, type }) {
    // 只读任务列表：`- [x]` 显示勾选状态，但点不动。
    return <input type={type} checked={Boolean(checked)} disabled readOnly />;
  },
  table({ children }) {
    return (
      <div className="chat-table-wrap">
        <table>{children}</table>
      </div>
    );
  },
};

/** 只放行明确列出的协议；其余链接退化成纯文字。 */
function safeUrl(url: string): string {
  const trimmed = url.trim();
  if (/^(https?:\/\/|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("#") || trimmed.startsWith("/")) return trimmed;
  return "";
}

interface Props {
  text: string;
  /** 宽阅读视图里用更大的行距与更宽的表格。 */
  wide?: boolean;
}

export default function ChatMarkdown({ text, wide = false }: Props) {
  return (
    <div className={wide ? "prose-chat prose-chat-wide" : "prose-chat"}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrl}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
