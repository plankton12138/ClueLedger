import { useEffect, useState, type ReactNode } from "react";

import { Button } from "../ui/Button";
import { failedStatuses, kinds, localTime, statusLabel } from "./labels";
import type { TraceDetailLoader, TraceEvent } from "./types";

function TraceSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="trace-section">
      <h5>{title}</h5>
      {children}
    </section>
  );
}

export function TraceDetailView({
  item,
  loader,
}: {
  item: TraceEvent;
  loader: TraceDetailLoader;
}) {
  const [source, setSource] = useState<Record<string, unknown>[]>([]);
  const [inputBody, setInputBody] = useState<unknown>();
  const [outputBody, setOutputBody] = useState<unknown>();
  const [attachmentLoading, setAttachmentLoading] = useState(false);
  const [capture, setCapture] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const { start_event_id: start, finish_event_id: finish } = item;

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    setInputBody(undefined);
    setOutputBody(undefined);
    setRawOpen(false);
    const ids = [...new Set([start, finish].filter(Boolean))] as string[];
    Promise.all([
      ...ids.map(id => loader.loadEvent(id)),
      loader.loadCaptureEnabled().catch(() => null),
    ])
      .then(value => {
        if (!current) return;
        setCapture((value.at(-1) as boolean | null) ?? null);
        setSource(value.slice(0, -1) as Record<string, unknown>[]);
      })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [loader, start, finish]);

  const first = source[0];
  const last = source.at(-1);
  const failed = failedStatuses.has(item.status);
  const eventText = item.summary
    ? `${item.error_code ? `[${item.error_code}] ${String(first?.where ?? item.name)}：` : ""}${item.summary}${
        first?.fix ? `。建议：${String(first.fix)}` : ""
      }`
    : "";
  const inputKey = typeof first?.input_detail === "string" ? first.input_detail : null;
  const resultKey =
    typeof last?.result_detail === "string"
      ? last.result_detail
      : typeof first?.result_detail === "string"
        ? first.result_detail
        : null;

  async function loadAttachment(key: string, target: "input" | "output") {
    setError("");
    setAttachmentLoading(true);
    try {
      const value = await loader.loadAttachment(key);
      if (target === "input") setInputBody(value);
      else setOutputBody(value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAttachmentLoading(false);
    }
  }

  const inputBlock = (() => {
    if (item.kind === "tool_call" && first?.args != null) {
      return <pre>{JSON.stringify(first.args, null, 2)}</pre>;
    }
    if (item.kind === "model") {
      if (inputKey) {
        return (
          <>
            <Button
              disabled={attachmentLoading}
              onClick={() => void loadAttachment(inputKey, "input")}
            >
              打开输入详情
            </Button>
            {inputBody !== undefined && (
              <pre>
                {typeof inputBody === "string" ? inputBody : JSON.stringify(inputBody, null, 2)}
              </pre>
            )}
          </>
        );
      }
      if (capture === false) {
        return (
          <p role="status">
            未保存模型输入。在活动记录设置中勾选「保存后续模型输入快照」后，新的轮次才会保留完整输入。
          </p>
        );
      }
      if (capture === true) {
        return <p role="status">本步没有输入附件。可能已过期，或当时未写入快照。</p>;
      }
      return <p role="status">正在确认是否保存了模型输入…</p>;
    }
    if (item.kind === "gate" && first?.where != null) {
      return (
        <p>
          {String(first.where)}
          {typeof first.input_tokens === "number" && typeof first.limit_tokens === "number"
            ? ` · 估算输入 ${first.input_tokens} / 上限 ${first.limit_tokens} Tokens`
            : ""}
        </p>
      );
    }
    if (item.kind === "retrieval" && Array.isArray(first?.provided)) {
      return <pre>{JSON.stringify(first.provided, null, 2)}</pre>;
    }
    return <p>无</p>;
  })();

  const outputBlock = (() => {
    if (item.kind === "tool_call" || item.kind === "write") {
      if (resultKey) {
        return (
          <>
            <Button
              disabled={attachmentLoading}
              onClick={() => void loadAttachment(resultKey, "output")}
            >
              打开结果详情
            </Button>
            {outputBody !== undefined && (
              <pre>
                {typeof outputBody === "string"
                  ? outputBody
                  : JSON.stringify(outputBody, null, 2)}
              </pre>
            )}
          </>
        );
      }
      if (item.summary && !failed) return <p>{item.summary}</p>;
      return <p>无</p>;
    }
    if (item.kind === "model") {
      return (
        <p>
          结束原因 {String(last?.stop_reason ?? "—")}
          {item.usage
            ? ` · Tokens ${item.usage.input ?? "—"} 入 / ${item.usage.output ?? "—"} 出`
            : ""}
        </p>
      );
    }
    if (item.kind === "gate") return <p>{statusLabel(item.status)}</p>;
    return <p>无</p>;
  })();

  return (
    <div className="trace-inspector" data-failed={failed || undefined}>
      <h4>
        {kinds[item.kind]} · {item.name} · {statusLabel(item.status)}
      </h4>
      <p>
        {item.point
          ? `发生于 ${localTime(item.started_at)}`
          : `开始 ${localTime(item.started_at)} · 结束 ${localTime(item.finished_at)}`}
      </p>
      {loading && <p role="status">正在读取事件详情…</p>}
      {error && <p role="alert">{error}</p>}
      {(failed || eventText) && (
        <TraceSection title="事件">
          {eventText ? (
            <p className={failed ? "trace-error" : undefined}>{eventText}</p>
          ) : (
            <p>无</p>
          )}
        </TraceSection>
      )}
      <TraceSection title="输入">{inputBlock}</TraceSection>
      <TraceSection title="输出">{outputBlock}</TraceSection>
      <TraceSection title="属性">
        <ul className="trace-attrs">
          <li>span_id {item.span_id}</li>
          <li>parent_span_id {item.parent_span_id ?? "—"}</li>
          <li>kind {item.kind}</li>
          {item.retry_of_span_id && <li>重试来源 {item.retry_of_span_id}</li>}
          {item.error_code && <li>error_code {item.error_code}</li>}
          {item.kind === "model" && <li>用途 {String(first?.stage ?? "—")}</li>}
          {item.kind === "model" &&
            typeof first?.tokens_before === "number" &&
            typeof first.tokens_after === "number" && (
              <li>
                重试前后估算输入 {first.tokens_before} → {first.tokens_after} Tokens
              </li>
            )}
          {item.usage && (
            <li>
              Tokens {item.usage.input ?? "—"} 入 / {item.usage.output ?? "—"} 出
            </li>
          )}
        </ul>
      </TraceSection>
      <div className="trace-toolbar">
        <Button onClick={() => setRawOpen(value => !value)}>
          {rawOpen ? "收起原始事件" : "原始事件"}
        </Button>
      </div>
      {rawOpen && <pre>{JSON.stringify(source, null, 2)}</pre>}
    </div>
  );
}
