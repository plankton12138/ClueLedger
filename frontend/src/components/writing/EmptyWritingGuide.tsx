/**
 * 新作品的轻量起步说明。
 *
 * 示例只把一句话放进当前对话草稿，不发送、不创建会话，也不直接改作品；因此对话
 * 仍是唯一创作入口。作者已经写了草稿时不覆盖，只把焦点带回输入框。
 */

import { ArrowRight, Sparkles } from "lucide-react";

const STARTERS = [
  "写第一章：主角抵达旅馆，雪已经封路。",
  "请帮我整理并保存这本书的全书结构。",
  "我还没想好，带我一步步想。",
] as const;

export default function EmptyWritingGuide({ onChoose }: { onChoose: (text: string) => void }) {
  return <div className="writing-empty-guide">
    <div className="writing-empty-guide-copy">
      <Sparkles className="writing-empty-guide-icon" size={28} strokeWidth={1.7} aria-hidden="true" />
      <h1>从正文或故事想法开始</h1>
      <p>可以直接写第一章，也可以先聊想法或整理全书结构。</p>
      <div className="writing-empty-examples" aria-label="可以这样说">
        {STARTERS.map(text => <button key={text} type="button" onClick={() => onChoose(text)}>
          “{text}”
        </button>)}
      </div>
      <p className="writing-empty-guide-hint"><ArrowRight size={15} aria-hidden="true" />在右侧对话栏说一句即可</p>
    </div>
  </div>;
}
