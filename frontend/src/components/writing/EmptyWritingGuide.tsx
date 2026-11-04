/**
 * 新作品的轻量起步说明。
 *
 * 示例只把一句话放进当前对话草稿，不发送、不创建会话，也不直接改作品；因此对话
 * 仍是唯一创作入口。作者已经写了草稿时不覆盖，只把焦点带回输入框。
 */

import { ArrowRight, Sparkles } from "lucide-react";

const STARTERS = [
  "我想写一个暴雪封山旅馆里的失踪案。",
  "我只有一个西部吸血鬼的念头，你自由发挥。",
  "我还没想好，带我一步步想。",
] as const;

export default function EmptyWritingGuide({ onChoose }: { onChoose: (text: string) => void }) {
  return <div className="writing-empty-guide">
    <div className="writing-empty-guide-copy">
      <Sparkles className="writing-empty-guide-icon" size={28} strokeWidth={1.7} aria-hidden="true" />
      <h1>从一句话开始</h1>
      <p>说说你想写什么。结构、章节和辅助资料由模型整理。</p>
      <div className="writing-empty-examples" aria-label="可以这样说">
        {STARTERS.map(text => <button key={text} type="button" onClick={() => onChoose(text)}>
          “{text}”
        </button>)}
      </div>
      <p className="writing-empty-guide-hint"><ArrowRight size={15} aria-hidden="true" />在右侧对话栏说一句即可</p>
    </div>
  </div>;
}
