/**
 * 意图提示条（V03-7.1）：只读展示一次表达的意图、写范围与关键选择。
 *
 * 界面上必须能一眼看出这次会不会改正式内容——讨论/候选标为只读；执行给出功能与
 * 写范围（或「写范围待选定」的 1–2 个关键选择）；同范围已授权时明确不重复审批。
 * 本组件不含任何写端口：是否继续执行由上层入口决定（执行入口随 V03-7.3）。
 */

import {
  intentFunctionLabel,
  intentLabels,
  intentSummary,
  type IntentPayload,
} from "../../api/intent";

export default function IntentNote({ payload }: { payload: IntentPayload }) {
  const fn = intentFunctionLabel(payload.function);
  return (
    <div className={`chat-intent ${payload.intent}`} role="note" aria-label="意图区分">
      <div className="chat-intent-head">
        <span className="chat-intent-chip">{intentLabels[payload.intent]}</span>
        {fn && <span className="chat-intent-fn">{fn}</span>}
        <span className="chat-intent-summary">{intentSummary(payload)}</span>
      </div>
      {payload.reason && <p className="chat-intent-reason">{payload.reason}</p>}
      {payload.choices.length > 0 && (
        <ul className="chat-intent-choices" aria-label="影响执行的关键选择">
          {payload.choices.map((choice) => (
            <li key={choice.where}>
              <b>{choice.question}</b>
              <span>{choice.options.join(" / ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
