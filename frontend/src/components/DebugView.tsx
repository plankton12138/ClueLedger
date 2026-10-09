/**
 * 事件流视图（P3 T9 调试视图，P5 T6 升级为导航一级入口）：原始事件帧滚动 +
 * 类型徽章 + 过滤 + 未知事件高亮。仅重排与样式对齐，功能不增强。
 * 可观测性是事件契约的一部分（四透镜之四）：未知事件显式上报，不静默吞。
 */

import { useMemo, useState } from "react";
import { AGENT_EVENT_TYPES } from "../api/types";
import { useChatStore } from "../state/store";

export default function DebugView() {
  const eventLog = useChatStore((s) => s.eventLog);
  const unhandled = useChatStore((s) => s.unhandledEventTypes);
  const running = useChatStore((s) => s.running);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return eventLog
      .map((frame, i) => ({ frame, i }))
      .filter(({ frame }) => q === "" || frame.type.toLowerCase().includes(q));
  }, [eventLog, filter]);

  return (
    <div className="flex h-full flex-col bg-ink">
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <span className="font-serif text-sm font-bold text-gold">事件流</span>
        <span className="text-xs text-paper/45">
          {eventLog.length} 帧{running ? " · 运行中" : ""}
        </span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按类型过滤…"
          className="ml-auto w-44 border border-line bg-ink-soft px-2 py-0.5 font-mono text-xs text-paper placeholder:text-paper/30 focus:border-gold focus:outline-none"
        />
      </div>
      {unhandled.length > 0 && (
        <div className="border-b border-overdue/50 bg-overdue/10 px-4 py-1.5 text-xs leading-relaxed text-overdue">
          契约外事件类型（显式上报）：{unhandled.join("、")}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-1 font-mono text-xs">
        {rows.length === 0 && (
          <p className="mt-6 text-center text-paper/30">暂无事件帧——连接后发一条指令即可看到原始流。</p>
        )}
        {rows.map(({ frame, i }) => {
          const known = (AGENT_EVENT_TYPES as readonly string[]).includes(frame.type);
          return (
            <div key={i} className="border-b border-line/30 py-1">
              <button
                type="button"
                onClick={() => setExpanded(expanded === i ? null : i)}
                className="flex w-full items-center gap-2 text-left"
              >
                <span className="w-7 shrink-0 text-paper/30">{i}</span>
                <span
                  className={`shrink-0 border px-1 ${
                    known ? "border-line text-gold-soft" : "border-overdue bg-overdue/10 text-overdue"
                  }`}
                >
                  {frame.type}
                </span>
                <span className="min-w-0 flex-1 truncate text-paper/40">{JSON.stringify(frame)}</span>
              </button>
              {expanded === i && (
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all border border-line bg-ink-soft p-2 text-paper/70">
                  {JSON.stringify(frame, null, 2)}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
