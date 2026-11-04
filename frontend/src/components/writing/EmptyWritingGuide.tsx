/**
 * 新作品的轻量起步说明。
 *
 * 示例只把一句话放进当前对话草稿，不发送、不创建会话，也不直接改作品；因此对话
 * 仍是唯一创作入口。作者已经写了草稿时不覆盖，只把焦点带回输入框。
 *
 * 没有全书结构时，这里先说明它是写正文的前提：模型会读取结构，再开始第一章。
 */

import { ArrowRight, Sparkles } from "lucide-react";

const STORY_STARTERS = [
  "我想写一个暴雪封山旅馆里的失踪案。",
  "我只有一个西部吸血鬼的念头，你自由发挥。",
  "我还没想好，带我一步步想。",
] as const;

const CHAPTER_STARTERS = [
  "写第一章：主角抵达旅馆，雪已经封路。",
  "从主角发现第一处异常开始写。",
  "这一章先交代人物关系和案发前夜。",
] as const;

export default function EmptyWritingGuide({
  onChoose,
  structureReady,
}: {
  onChoose: (text: string) => void;
  structureReady: boolean;
}) {
  const starters = structureReady ? CHAPTER_STARTERS : STORY_STARTERS;
  return <div className="writing-empty-guide">
    <div className="writing-empty-guide-copy">
      <Sparkles className="writing-empty-guide-icon" size={28} strokeWidth={1.7} aria-hidden="true" />
      <h1>{structureReady ? "结构已就绪，可以开始第一章" : "先定结构，再写正文"}</h1>
      <p>{structureReady
        ? "说说这一章要写什么。模型会按全书结构写正文。"
        : "全书结构是模型写正文前读取的全局依据。说一句你想写什么，模型会先整理结构，再开始第一章。"}</p>
      <div className="writing-empty-examples" aria-label="可以这样说">
        {starters.map(text => <button key={text} type="button" onClick={() => onChoose(text)}>
          “{text}”
        </button>)}
      </div>
      <p className="writing-empty-guide-hint"><ArrowRight size={15} aria-hidden="true" />在右侧对话栏说一句即可</p>
    </div>
  </div>;
}
