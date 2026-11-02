/**
 * 目标化模型候选（1.0.9 P4）。
 *
 * 模型动作只负责把候选文字交给作者，不自动写入正式内容。生成期间作者可以继续编辑；
 * 候选按目标保存，页面引用同一份状态。没有可用模型时动作可见但禁用，人工输入不受影响。
 */

import { Sparkles } from "lucide-react";

import { useWritingStore } from "../../state/writing";
import type { ModelSupportView } from "../../api/writing";
import { Button } from "../ui/Button";

const STATUS_LABEL: Record<string, string> = {
  streaming: "正在生成…",
  ready: "候选已就绪",
  stale: "生成后内容又变了，采用前请核对",
  failed: "生成未完成",
  discarded: "已放弃",
};

export default function ModelCandidate({
  assistKey,
  support,
  label,
  disabledReason,
  onStart,
  onAdopt,
  adoptLabel = "采用到输入",
  extraAdopt,
  extraAdoptLabel,
  readOnly = false,
}: {
  assistKey: string;
  support: ModelSupportView;
  label: string;
  disabledReason?: string;
  onStart: () => void;
  onAdopt: (text: string) => void;
  adoptLabel?: string;
  extraAdopt?: (text: string) => void;
  extraAdoptLabel?: string;
  /** 方向建议只是一段参考文字：可以阅读与复制，不进入正式数据。 */
  readOnly?: boolean;
}) {
  const state = useWritingStore(value => value.assists[assistKey]);
  const stop = useWritingStore(value => value.assistStop);
  const clear = useWritingStore(value => value.assistClear);
  const workspaceId = useWritingStore(value => value.workspaceId);
  const streaming = state?.status === "streaming";
  const reason = disabledReason ?? (!support.available ? support.disabled_reason ?? "当前没有可用模型" : undefined);
  return <div className="model-assist">
    <Button size="compact" variant="secondary" busy={streaming} disabled={Boolean(reason) || streaming} disabledReason={reason} onClick={onStart}>
      <Sparkles size={15} strokeWidth={1.8} aria-hidden="true" />{label}
    </Button>
    {reason && <p className="ui-field-hint">{reason}</p>}
    {state && state.status !== "idle" && <div className="model-candidate" data-status={state.status}>
      <p className="model-candidate-state">{STATUS_LABEL[state.status] ?? state.status}{state.stopping ? " · 正在停止" : ""}</p>
      {state.text && <blockquote className="model-candidate-text">{state.text}</blockquote>}
      {state.error && <p className="ui-field-error" role="alert">{state.error}</p>}
      <div className="model-candidate-actions">
        {streaming && <Button size="compact" onClick={() => { if (workspaceId && state.candidateId) void stop(workspaceId, assistKey); }} disabled={!state.candidateId || state.stopping}>停止</Button>}
        {state.text.trim() && !readOnly && <Button size="compact" variant="primary" onClick={() => onAdopt(state.text.trim())}>{adoptLabel}</Button>}
        {state.text.trim() && !readOnly && extraAdopt && extraAdoptLabel && <Button size="compact" onClick={() => extraAdopt(state.text.trim())}>{extraAdoptLabel}</Button>}
        {state.text.trim() && readOnly && <Button size="compact" onClick={() => { void navigator.clipboard?.writeText(state.text.trim()); }}>复制建议</Button>}
        {!streaming && <Button size="compact" variant="quiet" onClick={() => clear(assistKey)}>放弃候选</Button>}
      </div>
    </div>}
  </div>;
}
