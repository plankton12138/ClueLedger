/**
 * 输入框下方的两个用量入口（0.9 U3）。
 *
 * 两个数字回答不同问题，所以是两个入口而不是一个面板：上下文说「还能不能继续写」，
 * 累计说「这段对话花掉多少」。混在一起时，作者会拿累计值去猜剩余空间。
 *
 * 数据一律来自服务端聚合。组件只负责刷新时机与文案，不自己累计——前端再算一遍，
 * 刷新一次就会和实时推送对不上。
 */

import OperationControls from "./OperationControls";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useModelsStore } from "../../state/models";

import type { CompactionOperation, ConversationUsageView } from "../../api/contextUsage";
import {
  autoCompactionNote,
  breakdown,
  capacitySourceLabel,
  compactionNote,
  contextAmount,
  contextLabel,
  contextShare,
  formatCount,
  formatDuration,
  formatPercent,
  formatTokens,
  hasCapacity,
  inputLimitSourceLabel,
  isCompactionFinished,
  modelInputNote,
  modelWindowNote,
  usageDisplayState,
  usageLabel,
} from "../../api/contextUsage";
import {
  fetchConversationUsage,
  readCompaction,
  startCompaction,
  stopCompaction,
  WorkspaceApiError,
} from "../../api/workspace";

interface Props {
  workspaceId: string | null;
  conversationId: string | null;
  /** 变化即重新读取；父组件传事件游标，流式期间由本组件节流。 */
  refreshKey?: number;
}

type Panel = "context" | "usage" | null;

/** 事件很密，读得太勤没有意义；300ms 足够跟上阅读节奏。 */
const REFRESH_DELAY_MS = 300;

export default function ConversationUsageBar(props: Props) {
  return <ScopedUsageBar key={JSON.stringify([props.workspaceId, props.conversationId])} {...props} />;
}

function ScopedUsageBar({
  workspaceId,
  conversationId,
  refreshKey = 0,
}: Props) {
  const [view, setView] = useState<ConversationUsageView | null>(null);
  const modelRevision = useModelsStore((state) => state.revision);
  const [panel, setPanel] = useState<Panel>(null);
  // 压缩完成后要重新读一次：占用与累计都会变。
  const [tick, setTick] = useState(0);
  const [lastOperation, setLastOperation] = useState<string | null>(null);
  const [operation, setOperation] = useState<CompactionOperation | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [stopping, setStopping] = useState(false);
  // 明细收在二级展开里：先让作者看到已用与总量，再按需解释预算构成。
  const [detailsOpen, setDetailsOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [position, setPosition] = useState({ bottom: 32, maxHeight: 320, left: 0 });

  useLayoutEffect(() => {
    if (!panel || !root.current) return;
    const node = root.current;
    const composer = node.closest(".chat-composer") ?? node;
    const update = () => {
      const anchor = node.getBoundingClientRect();
      const top = composer.getBoundingClientRect().top;
      setPosition({ bottom: anchor.bottom - top + 8, maxHeight: Math.max(80, top - 16), left: Math.max(0, 8 - anchor.left) });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(composer);
    window.addEventListener("resize", update);
    return () => { observer.disconnect(); window.removeEventListener("resize", update); };
  }, [panel]);

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
            current && current.conversation_id === next.conversation_id && current.through_seq > next.through_seq ? current : next,
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
  }, [workspaceId, conversationId, refreshKey, tick, modelRevision]);

  useEffect(() => {
    if (panel !== null) setTick((value) => value + 1);
  }, [panel]);

  useEffect(() => {
    if (panel === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPanel(null);
        trigger.current?.focus();
      }
    };
    const onClick = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setPanel(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onClick);
    };
  }, [panel]);

  useEffect(() => {
    if (!view?.compaction) return;
    setLastOperation(view.compaction.operation_id);
    if (!isCompactionFinished(view.compaction)) setOperation(view.compaction);
    else setNote(compactionNote(view.compaction));
  }, [view]);

  useEffect(() => {
    if (!workspaceId || !conversationId || operation === null) return;
    if (isCompactionFinished(operation)) {
      setOperation(null);
      setNote(compactionNote(operation));
      setTick((value) => value + 1);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      readCompaction(workspaceId, conversationId, operation.operation_id)
        .then((next) => { if (!cancelled) setOperation(next); })
        .catch((error: unknown) => {
          if (!cancelled) {
            setNote(error instanceof Error ? error.message : "[CONTEXT_STATUS_FAILED] 上下文压缩：状态读取失败。建议：稍后重试。");
            // 临时断线不能把正在运行的操作丢掉；继续按同一标识查询。
            if (error instanceof WorkspaceApiError && error.status === 404) {
              setOperation(null);
              setTick((value) => value + 1);
            } else setOperation((current) => current ? { ...current } : null);
          }
        });
    }, 800);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [workspaceId, conversationId, operation]);

  const compact = useCallback(() => {
    if (!workspaceId || !conversationId || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setNote(null);
    startCompaction(workspaceId, conversationId, `compact-${Date.now()}`)
      .then(value => { setOperation(value); setLastOperation(value.operation_id); })
      .catch((error: unknown) => {
        setNote(error instanceof Error ? error.message : "压缩没有开始。");
      })
      .finally(() => { submittingRef.current = false; setSubmitting(false); });
  }, [workspaceId, conversationId]);

  const stop = useCallback(() => {
    if (!workspaceId || !conversationId || !operation || stopping) return;
    setStopping(true);
    stopCompaction(workspaceId, conversationId, operation.operation_id)
      .then(value => { setOperation(value); setLastOperation(value.operation_id); })
      .catch((error: unknown) => setNote(error instanceof Error ? error.message : "[CONTEXT_STOP_FAILED] 上下文压缩：停止失败。建议：重试停止。"))
      .finally(() => setStopping(false));
  }, [workspaceId, conversationId, operation, stopping]);

  const toggle = useCallback((next: Panel) => {
    setPanel((current) => (current === next ? null : next));
  }, []);

  const context = view?.context ?? null;
  const totals = view?.usage ?? null;
  const share = context ? contextShare(context) : null;
  const known = context ? hasCapacity(context) : false;
  const tight = context?.budget === "near" || context?.budget === "over";
  const items = context ? breakdown(context) : [];
  const compactionHint = context ? autoCompactionNote(context, view?.policy) : null;
  const modelInput = context ? modelInputNote(context) : null;

  return (
    <div className="relative flex min-w-0 items-center gap-1" ref={root}>
      <button
        type="button"
        className={`chat-usage-entry${tight ? " tight" : ""}`}
        aria-expanded={panel === "context"}
        aria-label="当前上下文占用"
        onClick={(event) => { trigger.current = event.currentTarget; toggle("context"); }}
      >
        {contextLabel(context)}
      </button>
      <button
        type="button"
        className="chat-usage-entry"
        aria-expanded={panel === "usage"}
        aria-label="本次对话累计用量"
        onClick={(event) => { trigger.current = event.currentTarget; toggle("usage"); }}
      >
        {usageLabel(totals)}
      </button>

      {panel === "context" && context && (
        <div className="chat-usage-pop" style={position} role="dialog" aria-label="当前上下文">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-semibold text-fg-strong">当前上下文</span>
            <span className="text-[11px] text-mute">
              {context.status === "running" ? "本轮进行中" : "估算"}
            </span>
          </div>
          <div className="mt-2 flex items-baseline justify-between gap-2 text-[11px]">
            <span className="text-fg-strong">{contextAmount(context)}</span>
            {known && share !== null && (
              <span className={tight ? "text-amber" : "text-mute"}>
                {formatPercent(share)}
              </span>
            )}
          </div>
          {known && share !== null && (
            <div className="chat-usage-track mt-1" aria-hidden="true">
              <div
                className={`chat-usage-fill${tight ? " tight" : ""}`}
                style={{ width: `${Math.min(100, Math.max(0, Math.round(share * 100)))}%` }}
              />
            </div>
          )}
          {context.estimated_active_tokens === null ? (
            <p className="mt-2 text-[11px] leading-5 text-mute">
              {known
                ? context.status === "pending"
                  ? "还没有发出请求；首次发送后按实际装配估算占用。"
                  : "当前模型的上下文待估算；下次发送时更新。"
                : "还没有可用的模型；先选择连接与模型。"}
            </p>
          ) : (
            <dl className="mt-2 space-y-0.5 text-[11px] text-mute">
              {items.map((item) => (
                <div className="flex justify-between gap-2" key={item.key}>
                  <dt>{item.label}</dt>
                  <dd className="text-fg-strong">{formatCount(item.tokens)}</dd>
                </div>
              ))}
            </dl>
          )}
          {context.output_reserve_tokens !== null && (
            <p className="mt-2 text-[10px] leading-4 text-mute">
              回复预留 {formatCount(context.output_reserve_tokens)} tokens
            </p>
          )}
          <button
            type="button"
            className="mt-1 text-[10px] font-bold text-blue"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((value) => !value)}
          >
            {detailsOpen ? "收起明细" : "容量明细"}
          </button>
          {detailsOpen && (
            <div className="mt-1 space-y-0.5 text-[10px] leading-4 text-mute">
              <p className="flex justify-between gap-2">
                <span>输入预算</span>
                <span>{formatCount(context.project_input_limit_tokens)} tokens</span>
              </p>
              <p className="flex justify-between gap-2">
                <span>模型窗口</span>
                <span>{modelWindowNote(context)}</span>
              </p>
              {modelInput !== null && (
                <p className="flex justify-between gap-2">
                  <span>模型可用输入</span>
                  <span>{modelInput}</span>
                </p>
              )}
              <p className="flex justify-between gap-2">
                <span>安全余量</span>
                <span>{formatCount(context.safety_tokens)} tokens</span>
              </p>
              <p className="flex justify-between gap-2">
                <span>生效上限</span>
                <span>{inputLimitSourceLabel(context.input_limit_source)}</span>
              </p>
              <p className="flex justify-between gap-2">
                <span>容量来源</span>
                <span>{capacitySourceLabel(context.capacity_source)}</span>
              </p>
              {compactionHint && <p>{compactionHint}</p>}
            </div>
          )}
          {!known && (
            <p className="mt-1 text-[10px] leading-4 text-mute">
              当前输入预算未获取，暂不显示百分比。
            </p>
          )}
          {context.status !== "pending" && (
            <div className="mt-2 flex items-center justify-between gap-2 border-t border-panel-head-edge pt-2">
              <button
                type="button"
                className="text-[10px] font-bold text-blue disabled:text-mute"
                disabled={operation !== null || submitting}
                onClick={compact}
              >
                {submitting ? "正在受理…" : operation === null ? "压缩上下文" : operation.state === "queued" ? "等待压缩…" : "正在压缩…"}
              </button>
              {operation && <button type="button" className="text-[10px] font-bold text-blue" disabled={stopping} onClick={stop}>{stopping ? "正在停止…" : "停止压缩"}</button>}
            </div>
          )}
          {context.status !== "pending" && lastOperation && <OperationControls key={lastOperation} operationId={lastOperation} onDone={() => setTick(value => value + 1)} />}
          {context.status !== "pending" && note && <p role="status" className="mt-1 text-[10px] leading-4 text-mute">{note}</p>}
        </div>
      )}

      {panel === "usage" && totals && (
        <div className="chat-usage-pop" style={position} role="dialog" aria-label="本次对话累计用量">
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
