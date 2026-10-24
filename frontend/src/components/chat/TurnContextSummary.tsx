/**
 * 本轮上下文注入（0.6 V06-E1）。
 *
 * 说明这一轮装进了哪些分区、各占多少字符、哪些被跳过、哪些候选没装进去。
 * 它是传输范围的事实，不证明模型理解或使用了这些内容；跳过与排除项都要写清原因与
 * 继续查看的入口，不能让「没给」看起来像「给了」。
 */

import type { TurnInjection, TurnInjectionPart } from "../../state/chatMessages";

const PART_LABEL: Record<string, string> = {
  instructions: "任务要求",
  catalog: "作品目录",
  modules: "已装模块",
  materials: "本轮材料",
  index: "章节索引",
  state: "身份披露",
  roster: "人物状态",
  timeline: "故事时间",
  clues: "未回收线索",
  glossary: "专有名词",
  settings: "设定集",
  history: "此前轮次",
};

const PROFILE_LABEL: Record<TurnInjection["profile"], string> = {
  writer: "写作",
  extract: "建索引",
  review: "检查",
  materials: "资料整理",
};

const SKIP_LABEL: Record<string, string> = {
  budget: "超出预算",
  empty: "没有内容",
};

const EXCLUDED_LABEL: Record<string, string> = {
  budget: "预算不足",
  limit: "超出条数",
};

function label(key: string): string {
  return PART_LABEL[key] ?? key;
}

function size(chars: number): string {
  return chars >= 1000 ? `${(chars / 1000).toFixed(1)}k` : `${chars}`;
}

function excludedCount(part: TurnInjectionPart): number {
  return part.excluded?.length ?? 0;
}

export default function TurnContextSummary({ injection }: { injection: TurnInjection }) {
  const skipped = injection.skipped;
  const hidden = injection.parts.reduce((total, part) => total + excludedCount(part), 0);
  const summary = `上下文 ${injection.parts.length} 个分区 · ${size(injection.used)} 字符`;
  return (
    <details className="mt-1 text-[11px] text-sub" data-testid="turn-injection">
      <summary className="cursor-pointer text-[10px] text-mute">
        {PROFILE_LABEL[injection.profile]}｜{summary}
        {skipped.length > 0 && `｜跳过 ${skipped.length} 项`}
        {hidden > 0 && `｜未装入 ${hidden} 条`}
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        {injection.parts.map((part) => (
          <li key={part.key} className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1">
              <span className="min-w-0 flex-1 truncate">{label(part.key)}</span>
              {excludedCount(part) > 0 && (
                <span className="shrink-0 whitespace-nowrap text-[10px] text-amber">
                  未装入 {excludedCount(part)} 条
                </span>
              )}
              <span className="shrink-0 whitespace-nowrap text-[10px] text-mute">
                {size(part.chars)} 字符
              </span>
            </div>
            {(part.excluded ?? []).map((item) => (
              <div
                key={`${part.key}:${item.id}`}
                className="flex items-center gap-1 pl-3 text-[10px]"
              >
                <span className="min-w-0 flex-1 truncate text-mute" title={item.location || item.id}>
                  {item.id}
                </span>
                {item.location && (
                  <span className="shrink-0 whitespace-nowrap text-mute">{item.location}</span>
                )}
                <span className="shrink-0 whitespace-nowrap text-amber">
                  {EXCLUDED_LABEL[item.reason] ?? item.reason}
                </span>
              </div>
            ))}
          </li>
        ))}
        {skipped.map((item) => (
          <li key={item.key} className="flex items-center gap-1">
            <span className="min-w-0 flex-1 truncate">{label(item.key)}</span>
            <span className="shrink-0 whitespace-nowrap text-[10px] text-amber">
              未注入：{SKIP_LABEL[item.reason] ?? item.reason}
              {item.chars ? `（${size(item.chars)} 字符）` : ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
