/**
 * 稿纸（0.4 V04-4）：正文与设定共用的只读 Markdown 阅读器。
 *
 * 只读是有意的：修改走对话里的 Agent 或应用之外的编辑器，界面不再提供第二条
 * 写入路径。`skipHtml` 关掉内嵌 HTML，避免正文里的标签被当成页面结构执行。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { FileView } from "../../api/workspace";
import CharacterBadges from "./CharacterBadges";

interface Props {
  file: FileView | null;
  loading: boolean;
}

export default function Paper({ file, loading }: Props) {
  if (!file) {
    return (
      <div className="grid h-full place-items-center px-8 text-center text-sm text-sub">
        <div>
          <p className="font-serif text-lg font-bold text-fg-strong">还没有打开文章</p>
          <p className="mt-2 text-xs">
            从左侧选择一章或一份设定；也可以在对话里直接说「写第一章」。
          </p>
        </div>
      </div>
    );
  }

  return (
    <article className="mx-auto flex h-full w-full max-w-3xl flex-col px-4 py-4">
      <header className="mb-3 flex flex-none flex-wrap items-baseline gap-x-3 gap-y-1 text-sub">
        <h2 className="min-w-0 truncate font-serif text-lg font-bold text-fg-strong">
          {file.display_title}
        </h2>
        <CharacterBadges roles={file.roles} />
        <span className="truncate text-[11px]">{file.path}</span>
        <span className="ml-auto shrink-0 text-[11px]">{file.size} 字节</span>
        {loading && <span className="text-[11px]">读取中…</span>}
        {file.metadata_warnings.length > 0 && (
          <p className="meta-warning basis-full">
            {file.metadata_warnings.join("；")}
          </p>
        )}
      </header>
      <div className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm">
        <div className="paper-lined min-h-full px-8 py-6">
          {file.display_text.trim() ? (
            <div className="prose-paper text-[15px] leading-8">
              <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                {file.display_text}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm italic text-[#8a8371]">这一页还是空的。</p>
          )}
        </div>
      </div>
    </article>
  );
}
