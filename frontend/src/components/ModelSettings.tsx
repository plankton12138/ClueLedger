import { useEffect, useRef, useState } from "react";
import { connectProvider, fetchModelChoices } from "../api/models";
import {
  fetchProviderCatalog,
  fetchProviders,
  upsertProvider,
  type CatalogEntry,
  type ProviderSummary,
} from "../api/rest";
import { useModelStore } from "../state/models";
import { message } from "../state/v2";
import LocalModelCard from "./LocalModelCard";
import { ErrorText, Notice } from "./Workbench";

type Category = CatalogEntry["category"] | "custom";
const groups: { key: Category; label: string }[] = [
  { key: "api", label: "厂商 API" },
  { key: "coding_plan", label: "订阅 / Coding Plan" },
  { key: "local", label: "本地模型" },
  { key: "custom", label: "自定义" },
];

export default function ModelSettings() {
  const current = useModelStore((s) => s.current);
  const selectionError = useModelStore((s) => s.error);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [category, setCategory] = useState<Category>("api");
  const [presetId, setPresetId] = useState("deepseek");
  const [profile, setProfile] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [useEnv, setUseEnv] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customLocal, setCustomLocal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [feedback, setFeedback] = useState("");
  const [filter, setFilter] = useState("");
  const generation = useRef(0);
  const preset = catalog.find((e) => e.name === presetId);
  const connected = providers.find((p) => p.name === profile);

  function choose(p: ProviderSummary, entries = catalog) {
    generation.current++;
    setProfile(p.name);
    setKey("");
    setError("");
    setFeedback("");
    setNotice("");
    setFilter("");
    const e = entries.find(
      (e) => e.name === (p.catalog_id || p.name) && e.base_url === p.base_url,
    );
    const value =
      current?.provider === p.name ? current.model : p.default_model;
    setModels(e?.models ?? []);
    setModel(value);
  }
  async function refreshProviders() {
    const rows = await fetchProviders();
    setProviders(rows);
    return rows;
  }
  useEffect(() => {
    let alive = true;
    void Promise.all([
      fetchProviderCatalog(),
      fetchProviders(),
      useModelStore.getState().refresh(),
    ])
      .then(([entries, rows]) => {
        if (!alive) return;
        setCatalog(entries);
        setProviders(rows);
        const state = useModelStore.getState().current;
        const initial =
          rows.find((p) => p.name === state?.provider) ??
          rows.find((p) => p.available);
        if (initial) {
          setProfile(initial.name);
          const e = entries.find(
            (e) => e.name === (initial.catalog_id || initial.name),
          );
          setModels(e?.models ?? []);
          setModel(
            state?.provider === initial.name
              ? state.model
              : initial.default_model,
          );
        }
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
      generation.current++;
    };
  }, []);

  async function perform(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setFeedback("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function discover(name = profile) {
    const epoch = ++generation.current;
    const result = await fetchModelChoices(name);
    if (epoch !== generation.current) return;
    setModels(result.models);
    setNotice(result.notice);
    setModel((previous) =>
      result.models.includes(previous) ? previous : (result.models[0] ?? ""),
    );
  }
  function changeCategory(value: Category) {
    setCategory(value);
    setKey("");
    setUseEnv(false);
    setError("");
    setFeedback("");
    setPresetId(catalog.find((p) => p.category === value)?.name ?? "");
  }
  async function useLocal(modelId: string) {
    await connectProvider("ollama", "");
    await useModelStore.getState().select("ollama", modelId);
    const rows = await refreshProviders();
    choose(rows.find((p) => p.name === "ollama")!);
    setModel(modelId);
  }
  const filtered = models.filter((m) =>
    m.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <div className="space-y-5 p-4">
      <Notice>
        当前：
        {current?.available
          ? `${current.display_name} · ${current.model}`
          : "尚未选择可用模型"}
        。 切换后服务中的后续任务使用新模型，正在执行的任务继续使用原模型。
      </Notice>
      <ErrorText>{error || selectionError}</ErrorText>
      <section className="space-y-3" aria-label="选择当前模型">
        <h3 className="text-sm font-bold">选择当前模型</h3>
        <label className="field">
          已连接来源
          <select
            aria-label="已连接来源"
            disabled={busy}
            className="well-input w-full p-2"
            value={profile}
            onChange={(e) => {
              const p = providers.find((p) => p.name === e.target.value);
              if (p) choose(p);
            }}
          >
            <option value="">先连接一个模型来源</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {catalog.find((e) => e.name === p.catalog_id)?.display_name ??
                  p.name}
                {p.available ? "" : " · 缺少密钥"}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end gap-2">
          <label className="field min-w-0 flex-1">
            模型
            <select
              aria-label="可选模型"
              className="well-input w-full p-2"
              value={model}
              disabled={busy || !connected}
              onChange={(e) => setModel(e.target.value)}
            >
              {!model && <option value="">请选择或刷新模型列表</option>}
              {model && !filtered.includes(model) && (
                <option value={model}>{model}</option>
              )}
              {filtered.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-gray px-3 py-2 text-xs"
            disabled={busy || !connected?.available}
            onClick={() => void perform(() => discover())}
          >
            刷新模型列表
          </button>
        </div>
        {models.length > 6 && (
          <input
            aria-label="筛选模型"
            placeholder="输入名称筛选模型"
            className="well-input w-full p-2 text-xs"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {notice && (
          <p role="status" className="text-xs leading-5 text-sub">
            {notice}
          </p>
        )}
        <details className="text-xs text-sub">
          <summary>手动指定模型编号</summary>
          <input
            aria-label="模型编号"
            className="well-input mt-2 w-full p-2"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </details>
        <button
          type="button"
          className="btn px-4 py-2 text-xs"
          disabled={busy || !connected?.available || !model.trim()}
          onClick={() =>
            void perform(async () => {
              await useModelStore.getState().select(profile, model);
              setFeedback(`已切换到 ${model}，后续任务生效。`);
            })
          }
        >
          使用此模型
        </button>
      </section>
      <section
        className="space-y-3 border-t border-line pt-4"
        aria-label="接入模型来源"
      >
        <h3 className="text-sm font-bold">接入模型来源</h3>
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label="接入方式"
        >
          {groups.map((g) => (
            <button
              type="button"
              key={g.key}
              aria-pressed={category === g.key}
              className={`btn px-3 py-2 text-xs ${category === g.key ? "" : "btn-gray"}`}
              disabled={busy || catalog.length === 0}
              onClick={() => changeCategory(g.key)}
            >
              {g.label}
            </button>
          ))}
        </div>
        {category !== "custom" ? (
          <>
            <label className="field">
              厂商或服务
              <select
                aria-label="厂商或服务"
                disabled={busy}
                className="well-input w-full p-2"
                value={presetId}
                onChange={(e) => {
                  setPresetId(e.target.value);
                  setKey("");
                  setUseEnv(false);
                  setError("");
                  setFeedback("");
                }}
              >
                {catalog
                  .filter((p) => p.category === category)
                  .map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.display_name}
                    </option>
                  ))}
              </select>
            </label>
            {preset?.note && (
              <p className="text-xs leading-5 text-sub">{preset.note}</p>
            )}
            {category === "coding_plan" && (
              <p className="text-xs leading-5 text-sub">
                订阅通过套餐接入 Key 连接，不等同于网页登录会员。普通 API
                与套餐密钥分别保存。
              </p>
            )}
            {preset && (
              <a
                href={preset.docs_url || preset.key_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-blue underline"
              >
                官方接入说明
              </a>
            )}
          </>
        ) : (
          <>
            <label className="field">
              连接名称
              <input
                aria-label="自定义连接名称"
                className="well-input p-2"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
              />
            </label>
            <label className="field">
              服务地址
              <input
                aria-label="自定义服务地址"
                type="url"
                className="well-input p-2"
                value={customUrl}
                onChange={(e) => setCustomUrl(e.target.value)}
                placeholder="http://localhost:8000/v1"
              />
            </label>
            <label className="flex gap-2 text-xs">
              <input
                type="checkbox"
                checked={customLocal}
                onChange={(e) => setCustomLocal(e.target.checked)}
              />
              本地免密服务
            </label>
          </>
        )}
        {(category === "custom" ? !customLocal : !preset?.no_auth) && (
          <>
            <label className="field">
              API Key（已连接时留空保留）
              <input
                aria-label="接入密钥"
                type="password"
                autoComplete="new-password"
                className="well-input p-2"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            {preset && category !== "custom" && (
              <>
                <a
                  href={preset.key_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue underline"
                >
                  前往厂商获取密钥
                </a>
                <label className="flex gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={useEnv}
                    onChange={(e) => setUseEnv(e.target.checked)}
                  />
                  使用服务端环境变量 {preset.api_key_env}
                </label>
              </>
            )}
          </>
        )}
        <button
          type="button"
          className="btn px-4 py-2 text-xs"
          disabled={
            busy ||
            (category === "custom"
              ? !customName.trim() || !customUrl.trim()
              : !preset)
          }
          onClick={() =>
            void perform(async () => {
              const saved =
                category === "custom"
                  ? await upsertProvider(customName.trim(), {
                      base_url: customUrl.trim(),
                      api_key: key,
                      default_model: "",
                      no_auth: customLocal,
                    })
                  : await connectProvider(presetId, key, useEnv);
              setKey("");
              await refreshProviders();
              choose(saved);
              setFeedback("连接已保存。选择模型并点击“使用此模型”即可切换。");
              if (saved.no_auth) await discover(saved.name);
            })
          }
        >
          保存连接
        </button>
        <p className="text-[11px] leading-5 text-sub">
          密钥仅保存于本机服务配置，不写入浏览器存储。保存连接不代表已验证模型调用。
        </p>
        {category === "local" && presetId === "ollama" && (
          <LocalModelCard
            onProvidersChanged={async () => {
              await refreshProviders();
            }}
            onModelSwitched={() => {}}
            onUseModel={useLocal}
          />
        )}
      </section>
      {feedback && (
        <p role="status" className="text-xs leading-5 text-green">
          {feedback}
        </p>
      )}
    </div>
  );
}
