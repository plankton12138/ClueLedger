import { Button } from "../ui/Button";
/**
 * 模型设置对话框（0.4 V04-9C）。
 *
 * 应用级状态：没有打开作品也能用。左边是已保存的连接，右边按「选择供应商 →
 * 填写连接 → 选择模型并保存」推进；编辑已有连接时跳过第一步。
 *
 * 「设为当前」是独立动作：保存连接不改变当前选择，删除当前连接则进入未选择状态
 * （界面先说明这一点，不自动换到下一个连接）。
 */

import { useEffect, useState } from "react";

import type { ModelConnection, ProviderPreset } from "../../api/models";
import { useModelsStore } from "../../state/models";
import { AppDialog } from "../ui/AppDialog";
import ConnectionForm from "./ConnectionForm";
import ProviderPicker from "./ProviderPicker";

type Mode =
  | { kind: "list" }
  | { kind: "provider" }
  | { kind: "form"; preset: ProviderPreset; connection: ModelConnection | null };

function presetFor(presets: ProviderPreset[], catalogId: string): ProviderPreset | undefined {
  return presets.find((item) => item.id === catalogId) ?? presets.find((item) => item.custom);
}

/** 列表项里的凭据摘要：订阅连接显示账号，API Key 连接显示尾四位。 */
function credentialLabel(connection: ModelConnection): string {
  if (connection.auth_method === "subscription") {
    if (connection.auth_status === "missing") return "未登录";
    const account = connection.account || "已登录";
    return connection.auth_status === "expired" ? `${account}（待续期）` : account;
  }
  return connection.has_key ? `Key ${connection.masked_key}` : "未配置 Key";
}

export default function ModelSettingsDialog() {
  const open = useModelsStore((state) => state.settingsOpen);
  const presets = useModelsStore((state) => state.presets);
  const connections = useModelsStore((state) => state.connections);
  const selection = useModelsStore((state) => state.selection);
  const loading = useModelsStore((state) => state.loading);
  const error = useModelsStore((state) => state.error);
  const close = useModelsStore((state) => state.closeSettings);
  const refresh = useModelsStore((state) => state.refresh);
  const remove = useModelsStore((state) => state.remove);
  const choose = useModelsStore((state) => state.choose);

  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setMode({ kind: "list" });
    setConfirming(null);
    // 打开时只读取本机配置，不自动调用任何供应商。
    void refresh();
  }, [open, refresh]);

  if (!open) return null;

  const sessionOnly = connections.some((item) => item.credential_storage === "session");

  const startEdit = (connection: ModelConnection) => {
    const preset = presetFor(presets, connection.catalog_id);
    if (!preset) return;
    setMode({ kind: "form", preset, connection });
  };

  return (
    <AppDialog open={open} onOpenChange={value => { if (!value) close(); }} title="模型设置" returnFocus={() => document.querySelector<HTMLElement>('[data-dialog-return="settings"]')} className="model-settings-dialog">
        {sessionOnly && (
          <p className="flex-none border-b border-[#e5d3a8] bg-[#f7efd9] px-5 py-2 text-sm text-[#6b5525]">
            系统凭据存储不可用：API Key 只在本次运行有效，重启后需要重新填写。
          </p>
        )}
        {error && (
          <p role="alert" className="flex-none border-b border-[#c98b8b] bg-[#f7e6e6] px-5 py-2 text-sm text-[#8a3b3b]">
            {error}
          </p>
        )}

        <div className="model-settings-grid">
          <aside className="min-h-0 overflow-auto border-r border-[#e2dbc6] bg-white/40 p-3">
            <div className="flex items-center gap-2">
              <h3 className="flex-1 text-sm font-bold tracking-wide text-ui-muted">
                连接（{connections.length}）
              </h3>
              <Button
                className="px-2 py-0.5 text-sm"
                onClick={() => setMode({ kind: "provider" })}
              >
                新增
              </Button>
            </div>

            <ul className="mt-2 space-y-1">
              {connections.map((connection) => {
                const current = selection.connection_id === connection.name;
                return (
                  <li
                    key={connection.name}
                    className={`rounded border px-2 py-2 ${
                      current ? "border-[#a8bfd0] bg-[#eef4f8]" : "border-[#e2dbc6] bg-white/70"
                    }`}
                  >
                    <div className="flex items-center gap-1">
                      <span className="min-w-0 flex-1 truncate text-xs font-bold text-[#3f3a2c]">
                        {connection.display_name}
                      </span>
                      {current && (
                        <span className="rounded bg-[#d7e6f0] px-1 py-0.5 text-[12px] text-[#2f5570]">
                          当前
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 truncate text-sm text-ui-muted">
                      {connection.models.length} 个模型 · {credentialLabel(connection)}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-sm">
                      <Button
                        className="underline text-[#3a6b8c]"
                        disabled={loading || !connection.has_key}
                        onClick={() => void choose(connection.name, connection.default_model)}
                      >
                        设为当前
                      </Button>
                      <Button className="underline text-ui-muted" onClick={() => startEdit(connection)}>
                        编辑
                      </Button>
                      {confirming === connection.name ? (
                        <>
                          <span className="text-[#8a3b3b]">
                            {current ? "会清空当前选择，确认删除？" : "确认删除？"}
                          </span>
                          <Button
                            className="underline text-[#8a3b3b]"
                            onClick={() => {
                              setConfirming(null);
                              void remove(connection.name);
                            }}
                          >
                            删除
                          </Button>
                          <Button className="underline" onClick={() => setConfirming(null)}>
                            取消
                          </Button>
                        </>
                      ) : (
                        <Button
                          className="underline text-[#8a3b3b]"
                          onClick={() => setConfirming(connection.name)}
                        >
                          删除
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
              {connections.length === 0 && (
                <li className="px-1 py-2 text-sm leading-5 text-ui-muted">
                  还没有连接。点「新增」选择一个供应商，填入 API Key 后即可开始对话。
                </li>
              )}
            </ul>
          </aside>

          <section className="min-h-0 overflow-auto p-4">
            {mode.kind === "list" && (
              <div className="grid h-full place-items-center text-center">
                <div>
                  <p className="font-serif text-base text-[#3f3a2c]">
                    {connections.length === 0 ? "连接一个模型供应商" : "选择左侧连接进行编辑"}
                  </p>
                  <p className="mt-1 text-sm leading-5 text-ui-muted">
                    「连接」是你在某家服务的账号；同一个供应商可以建多个连接。
                  </p>
                  <Button
                    variant="primary" className="mt-3 px-3 py-1 text-xs"
                    onClick={() => setMode({ kind: "provider" })}
                  >
                    新增连接
                  </Button>
                </div>
              </div>
            )}

            {mode.kind === "provider" && (
              <ProviderPicker
                presets={presets}
                onPick={(preset) => setMode({ kind: "form", preset, connection: null })}
              />
            )}

            {mode.kind === "form" && (
              <ConnectionForm
                key={mode.connection?.name ?? mode.preset.id}
                preset={mode.preset}
                connection={mode.connection}
                onBack={() => setMode({ kind: "provider" })}
                onDone={() => setMode({ kind: "list" })}
              />
            )}
          </section>
        </div>
    </AppDialog>
  );
}
