/**
 * 输入框下方的两个用量入口（0.9 U3）。
 *
 * 两个数字回答不同问题，所以是两个入口而不是一个面板：上下文说「还能不能继续写」，
 * 累计说「这段对话花掉多少」。混在一起时，作者会拿累计值去猜剩余空间。
 *
 * 数据一律来自服务端聚合。组件只负责刷新时机与文案，不自己累计——前端再算一遍，
 * 刷新一次就会和实时推送对不上。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { CompactionOperation, ConversationUsageView } from "../../api/contextUsage";
import {
  breakdown,
  compactionNote,
  contextLabel,
  contextShare,
  formatDuration,
  formatPercent,
  formatTokens,
  isCompactionFinished,
  usageDisplayState,
  usageLabel,
} from "../../api/contextUsage";
import {
  fetchConversationUsage,
  readCompaction,
  startCompaction,
} from "../../api/workspace";

interface Props {
  workspaceId: string | null;
  conversationId: string | null;
  /** 变化即重新读取；父组件传事件游标，流式期间由本组件节流。 */
  refreshKey?: number;
  onOpenModelSettings?: () => void;
}

type Panel = "context" | "usage" | null;

/** 事件很密，读得太勤没有意义；300ms 足够跟上阅读节奏。 */
const REFRESH_DELAY_MS = 300;

export default function ConversationUsageBar({
  workspaceId,
  conversationId,
  refreshKey = 0,
  onOpenModelSettings,
}: Props) {
  const [view, setView] = useState<ConversationUsageView | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  // 整理完成后要重新读一次：占用与累计都会变。
  const [tick, setTick] = useState(0);
  const [operation, setOperation] = useState<CompactionOperation | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const root = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!workspaceId || !conversationId) {
      setView(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      fetchConversationUsage(workspaceId, conversationId)
        .then((next) => {
          if (cancelled) return;
          // 旧响应不能盖掉更新的游标：流式与手动刷新会并发。
          setView((current) =>
            current && current.through_seq > next.through_seq ? current : next,
          );
        })
        .catch(() => {
          // 读取失败不打断写作：保留上一次的数字，入口照常可用。
          if (!cancelled) setView((current) => current);
        });
    }, REFRESH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [workspaceId, conversationId, refreshKey, tick]);

  useEffect(() => {
    if (panel === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPanel(null);
    };
    const onClick = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setPanel(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [panel]);

  useEffect(() => {
    if (!workspaceId || !conversationId || operation === null) return;
    if (isCompactionFinished(operation)) {
      setOperation(null);
      setNote(compactionNote(operation));
      setTick((value) => value + 1);
      return;
    }
    const timer = setTimeout(() => {
      readCompaction(workspaceId, conversationId, operation.operation_id)
        .then(setOperation)
        .catch(() => setOperation(null));
    }, 800);
    return () => clearTimeout(timer);
  }, [workspaceId, conversationId, operation]);

  const compact = useCallback(() => {
    if (!workspaceId || !conversationId) return;
    setNote(null);
    startCompaction(workspaceId, conversationId, `compact-${Date.now()}`)
      .then(setOperation)
      .catch((error: unknown) => {
        setNote(error instanceof Error ? error.message : "整理没有开始。");
      });
  }, [workspaceId, conversationId]);

  const toggle = useCallback((next: Panel) => {
    setPanel((current) => (current === next ? null : next));
  }, []);

  const context = view?.context ?? null;
  const totals = view?.usage ?? null;
  const share = context ? contextShare(context) : null;
  const tight = context?.budget === "near" || context?.budget === "over";
  const items = context ? breakdown(context) : [];

  return (
    <div className="relative flex min-w-0 items-center gap-1" ref={root}>
      <button
        type="button"
        className={`chat-usage-entry${tight ? " tight" : ""}`}
        aria-expanded={panel === "context"}
        aria-label="当前上下文占用"
        onClick={() => toggle("context")}
      >
        {contextLabel(context)}
      </button>
      <button
        type="button"
        className="chat-usage-entry"
        aria-expanded={panel === "usage"}
        aria-label="本次对话累计用量"
        onClick={() => toggle("usage")}
      >
        {usageLabel(totals)}
      </button>

      {panel === "context" && context && (
        <div className="chat-usage-pop" role="dialog" aria-label="当前上下文">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-semibold text-fg-strong">当前上下文</span>
            <span className="text-[11px] text-mute">
              {context.status === "running" ? "本轮进行中" : "估算"}
            </span>
          </div>
          {context.status === "pending" ? (
            <p className="mt-2 text-[11px] leading-5 text-mute">
              还没有发出请求。首次发送后按实际装配估算占用。
            </p>
          ) : (
            <>
              <div className="mt-2 flex items-baseline justify-between text-[11px]">
                <span className="text-fg-strong">
                  ≈{formatTokens(context.estimated_active_tokens)}
                  {share !== null && ` / ${formatTokens(context.context_window_tokens)}`}
                </span>
                <span className={tight ? "text-amber" : "text-mute"}>
                  {share === null ? "容量未知" : formatPercent(share)}
                </span>
              </div>
              <div className="chat-usage-track mt-1" aria-hidden="true">
                <div
                  className={`chat-usage-fill${tight ? " tight" : ""}`}
                  style={{ width: `${Math.min(100, Math.round((share ?? 0) * 100))}%` }}
                />
              </div>
              <dl className="mt-2 space-y-0.5 text-[11px] text-mute">
                {items.map((item) => (
                  <div className="flex justify-between" key={item.key}>
                    <dt>{item.label}</dt>
                    <dd className="text-fg-strong">{formatTokens(item.tokens)}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 text-[10px] leading-4 text-mute">
                回复预留 {formatTokens(context.output_reserve_tokens)}，不计入已用输入。
              </p>
              <div className="mt-2 flex items-center justify-between gap-2 border-t border-panel-head-edge pt-2">
                <button
                  type="button"
                  className="text-[10px] font-bold text-blue disabled:text-mute"
                  disabled={operation !== null}
                  onClick={compact}
                >
                  {operation === null ? "整理上下文" : "正在整理…"}
                </button>
                {note && <span className="min-w-0 flex-1 truncate text-[10px] text-mute">{note}</span>}
              </div>
              {context.context_window_tokens === null && onOpenModelSettings && (
                <button
                  type="button"
                  className="mt-2 text-[10px] font-bold text-blue"
                  onClick={onOpenModelSettings}
                >
                  在模型设置里填写容量
                </button>
              )}
            </>
          )}
        </div>
      )}

      {panel === "usage" && totals && (
        <div className="chat-usage-pop" role="dialog" aria-label="本次对话累计用量">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-semibold text-fg-strong">本次对话</span>
            <span className="text-[11px] text-mute">{totals.finished_requests} 次请求</span>
          </div>
          {usageDisplayState(totals) === "unknown" && (
            <p className="mt-2 text-[11px] leading-5 text-mute">
              供应商没有返回用量，累计值未知。
            </p>
          )}
          {usageDisplayState(totals) === "empty" && (
            <p className="mt-2 text-[11px] leading-5 text-mute">还没有发出请求。</p>
          )}
          {totals.total_tokens !== null && (
            <dl className="mt-2 space-y-0.5 text-[11px] text-mute">
              <div className="flex justify-between">
                <dt>输出</dt>
                <dd className="text-fg-strong">{formatTokens(totals.output_tokens)}</dd>
              </div>
              <div className="flex justify-between">
                <dt>输入</dt>
                <dd className="text-fg-strong">{formatTokens(totals.input_tokens)}</dd>
              </div>
              <div className="flex justify-between">
                <dt>其中缓存读取</dt>
                <dd className="text-fg-strong">{formatTokens(totals.cache_read_tokens)}</dd>
              </div>
            </dl>
          )}
          <p className="mt-2 flex justify-between text-[10px] leading-4 text-mute">
            <span>模型用时 {formatDuration(totals.model_duration_ms)}</span>
            <span>{formatTokens(totals.total_tokens)}</span>
          </p>
          <p className="mt-1 text-[10px] leading-4 text-mute">
            {totals.requests_without_usage > 0
              ? `${totals.requests_without_usage} 次请求未统计`
              : "全部请求都有用量"}
            {totals.requests_in_progress > 0 && `；${totals.requests_in_progress} 次进行中`}
          </p>
        </div>
      )}
    </div>
  );
}
