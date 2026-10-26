/**
 * 日常模型选择（0.4 V04-9C）。
 *
 * 对话栏顶部显示当前「连接 / 模型」，切换只影响下一轮：正在运行的一轮用它自己
 * 受理时的快照。切换以后端返回为准（`choose` 成功才更新界面），不先改本地再等结果。
 *
 * 没有连接时给一个入口，而不是一个空下拉框：作者要知道下一步做什么。
 */

import { useModelsStore } from "../../state/models";
import type { ThinkingLevel } from "../../api/models";

const THINKING_LABELS: Record<ThinkingLevel, string> = {
  off: "关闭", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最大",
};

export default function ModelSelector() {
  const connections = useModelsStore((state) => state.connections);
  const selection = useModelsStore((state) => state.selection);
  const loading = useModelsStore((state) => state.loading);
  const choose = useModelsStore((state) => state.choose);
  const openSettings = useModelsStore((state) => state.openSettings);
  const error = useModelsStore((state) => state.error);
  const conflict = useModelsStore((state) => state.conflict);
  const refresh = useModelsStore((state) => state.refresh);

  if (connections.length === 0) {
    return (
      <button
        className="btn btn-gray px-2 py-1 text-[11px]"
        onClick={openSettings}
      >
        连接模型…
      </button>
    );
  }

  const value =
    selection.connection_id && selection.model
      ? `${selection.connection_id}::${selection.model}`
      : "";

  const sourceLabel =
    selection.source === "startup"
      ? "（启动参数指定）"
      : selection.source === "automatic"
        ? "（默认连接）"
        : "";

  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
      <select
        aria-label="当前连接与模型"
        className="well-input min-w-0 flex-1 basis-[9em] px-2 py-1 text-[11px]"
        value={value}
        disabled={loading}
        onChange={(event) => {
          const [connectionId, model] = event.target.value.split("::");
          if (connectionId && model) void choose(connectionId, model);
        }}
        title={selection.available ? `来源：${selection.source}${sourceLabel}` : selection.reason}
      >
        {!value && <option value="">未选择模型</option>}
        {connections.map((connection) => (
          <optgroup key={connection.name} label={connection.display_name}>
            {(connection.models.length ? connection.models : [connection.default_model])
              .filter(Boolean)
              .map((model) => (
                <option key={model} value={`${connection.name}::${model}`}>
                  {model}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
      <div className="flex shrink-0 items-center gap-1">
      <select
        aria-label="思考深度"
        className="well-input w-[7.5em] px-1 py-1 text-[11px]"
        value={selection.thinking_level ?? ""}
        disabled={loading || !selection.available || !selection.thinking_levels?.length}
        title={selection.thinking_levels?.length
          ? "默认沿用供应商设置；修改从下一轮生效"
          : "当前模型尚未提供可调思考深度"}
        onChange={(event) => {
          if (selection.connection_id) {
            void choose(selection.connection_id, selection.model,
              (event.target.value || null) as ThinkingLevel | null);
          }
        }}
      >
        <option value="">思考：默认</option>
        {(selection.thinking_levels ?? []).map((level) => (
          <option key={level} value={level}>思考：{THINKING_LABELS[level]}</option>
        ))}
      </select>
      <button
        className="px-1 py-1 text-[10px] font-bold text-blue hover:underline"
        onClick={openSettings}
        title="管理连接"
      >
        管理
      </button>
      </div>
      {error && (
        <div role="alert" className="basis-full text-xs text-[#8a3b3b]">
          {error}
          {conflict && (
            <button className="ml-1 underline" disabled={loading} onClick={() => void refresh()}>
              重新读取
            </button>
          )}
        </div>
      )}
    </div>
  );
}
