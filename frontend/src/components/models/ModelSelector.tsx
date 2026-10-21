/**
 * 日常模型选择（0.4 V04-9C）。
 *
 * 对话栏顶部显示当前「连接 / 模型」，切换只影响下一轮：正在运行的一轮用它自己
 * 受理时的快照。切换以后端返回为准（`choose` 成功才更新界面），不先改本地再等结果。
 *
 * 没有连接时给一个入口，而不是一个空下拉框：作者要知道下一步做什么。
 */

import { useModelsStore } from "../../state/models";

export default function ModelSelector() {
  const connections = useModelsStore((state) => state.connections);
  const selection = useModelsStore((state) => state.selection);
  const loading = useModelsStore((state) => state.loading);
  const choose = useModelsStore((state) => state.choose);
  const openSettings = useModelsStore((state) => state.openSettings);

  if (connections.length === 0) {
    return (
      <button
        className="rounded border border-[#4c7a9c] bg-[#1b4666] px-2 py-1 text-[11px] text-white/85 hover:bg-[#25557a]"
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
    <div className="flex min-w-0 items-center gap-1">
      <select
        aria-label="当前连接与模型"
        className="min-w-0 max-w-[220px] rounded bg-[#1b4666] px-2 py-1 text-[11px] text-white"
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
      <button
        className="rounded border border-[#355f7f] px-2 py-1 text-[10px] text-white/70 hover:bg-[#1b4666]"
        onClick={openSettings}
        title="管理连接"
      >
        管理
      </button>
    </div>
  );
}
