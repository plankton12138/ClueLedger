/**
 * 本轮带上来的灵感（0.8 I3）。
 *
 * 依据是受理事件里冻结的引用，不是模型措辞：「带上了这一版」与「模型读完并采用了」
 * 是两件事，这里只陈述前者。预览只是短文本，看全文要到灵感面板。
 */

import type { TurnIdea } from "../../state/chatMessages";

export default function TurnIdeas({ ideas }: { ideas: TurnIdea[] }) {
  if (ideas.length === 0) return null;
  return (
    <details className="mt-1 text-[11px] text-sub" data-testid="turn-ideas">
      <summary className="cursor-pointer text-[10px] text-mute">
        本轮灵感 {ideas.length} 条
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        {ideas.map((item) => (
          <li key={`${item.idea_id}:${item.revision_id}`} className="min-w-0 truncate">
            {item.preview || "（没有正文）"}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] text-mute">
        只表示这一轮带上了这条灵感，不表示模型读完了全文。
      </p>
    </details>
  );
}
