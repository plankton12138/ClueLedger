/**
 * 同页证据栏（0.7）。
 *
 * 资料条目右侧显示原文依据：章节、引文与当前有效性。来源改过时不按旧下标高亮新文本，
 * 只显示保存的引文并标注「来源已修改」；「打开原文」交给写作页按当前内容阅读。
 */

import type { MaterialEntry } from "../../api/storyMaterials";
import { FRESHNESS_LABEL, FRESHNESS_TONE } from "./labels";
import { Button } from "../ui/Button";

interface Props {
  entry: MaterialEntry;
  onOpenSource: (path: string) => void;
  onTakeToChat: (path: string) => void;
  onClose: () => void;
  showClose?: boolean;
}

export default function EvidencePane({ entry, onOpenSource, onTakeToChat, onClose, showClose = true }: Props) {
  const sourcePath = entry.evidence[0]?.path ?? entry.entity_path ?? null;
  return (
    <aside className="flex min-h-0 w-full flex-1 flex-col border-t border-panel-edge bg-surface md:border-t-0">
      <div className="flex flex-none items-start gap-2 border-b border-panel-edge px-3 py-2">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-bold" title={entry.title}>
            {entry.title}
          </h2>
          {entry.subtitle && <p className="truncate text-xs text-sub">{entry.subtitle}</p>}
        </div>
        {showClose && <Button size="compact" onClick={onClose}>
          关闭
        </Button>}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2 text-sm">
        {entry.notes.length > 0 && (
          <ul className="mb-2 space-y-1 text-xs text-amber">
            {entry.notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        )}

        <h3 className="mb-1 font-bold text-sub">原文依据</h3>
        {entry.evidence.length === 0 ? (
          <p className="text-mute">
            {entry.coverage === "empty"
              ? "这一项是整理后的无发现结果，没有原文依据。"
              : entry.coverage === "insufficient"
                ? "整理时信息不足，没有可引用的原文。"
                : "还没有可用的原文依据。"}
          </p>
        ) : (
          <ul className="space-y-2">
            {entry.evidence.map((evidence, index) => (
              <li key={`${evidence.path}:${evidence.start}:${index}`} className="rounded border border-row-edge p-2">
                <div className="flex items-center gap-2 text-xs text-mute">
                  <span className="min-w-0 flex-1 truncate" title={evidence.path}>
                    {evidence.chapter !== null ? `第 ${evidence.chapter} 章｜` : ""}
                    {evidence.path}
                  </span>
                  {evidence.freshness !== "valid" && (
                    <span className={FRESHNESS_TONE[evidence.freshness]}>
                      {FRESHNESS_LABEL[evidence.freshness]}
                    </span>
                  )}
                </div>
                <blockquote className="mt-1 border-l-2 border-sheet-line pl-2 font-serif text-sm leading-6 text-fg">
                  {evidence.quote}
                </blockquote>
                {evidence.freshness === "stale" && (
                  <p className="mt-1 text-xs text-amber">
                    这是整理时保存的引文；原文已经改过，不能按旧位置高亮当前内容。
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-none flex-wrap gap-2 border-t border-panel-edge px-3 py-2">
        <button
          className="btn btn-gray px-2 py-1 text-sm"
          disabled={!sourcePath}
          onClick={() => sourcePath && onOpenSource(sourcePath)}
        >
          打开原文
        </button>
        <button
          className="btn btn-gray px-2 py-1 text-sm"
          disabled={!sourcePath}
          onClick={() => sourcePath && onTakeToChat(sourcePath)}
          title="把这份原文加入写作草稿的参考，不自动发送"
        >
          带到对话
        </button>
      </div>
    </aside>
  );
}
