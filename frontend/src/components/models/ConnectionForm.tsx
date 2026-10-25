import { Button } from "../ui/Button";
import { errorMessage } from "../../api/errors";
/**
 * 连接表单（0.4 V04-9C 第二、三步）。
 *
 * 三个事实分开显示，不能用一个绿色「已连接」概括：连接已保存、凭据已配置、
 * 响应测试通过、工具测试通过。测试结果与「地址 + 协议 + Key + 模型」绑定，
 * 其中任何一项被改动后立刻回到「未测试」。
 *
 * Key 只在这个组件里短暂存在：保存成功后清空，不进入全局状态、不写入浏览器存储。
 * 编辑已有连接时留空表示沿用旧 Key——界面不会把脱敏值回填进密码框。
 */

import { useMemo, useState } from "react";

import {
  discoverModels,
  probeModel,
  type ConnectionDraft,
  type ModelConnection,
  type ProviderApi,
  type ProviderPreset,
} from "../../api/models";
import { useModelsStore } from "../../state/models";
import { defaultConnectionName } from "./naming";

interface Props {
  connection: ModelConnection | null;
  preset: ProviderPreset;
  onBack(): void;
  onDone(): void;
}

interface TestResult {
  fingerprint: string;
  response: "passed" | "failed" | "untested";
  toolCall: "passed" | "failed" | "untested";
  message: string;
}

const API_LABEL: Record<ProviderApi, string> = {
  "openai-completions": "OpenAI 兼容（Completions）",
  "openai-responses": "OpenAI Responses",
  "anthropic-messages": "Anthropic Messages",
  "google-generative-ai": "Google 原生接口",
};

function describe(error: unknown): string {
  return errorMessage(error, "模型连接");
}

export default function ConnectionForm({ connection, preset, onBack, onDone }: Props) {
  const create = useModelsStore((state) => state.create);
  const update = useModelsStore((state) => state.update);
  const conflict = useModelsStore((state) => state.conflict);
  const refresh = useModelsStore((state) => state.refresh);

  const connections = useModelsStore((state) => state.connections);

  const [stage, setStage] = useState<"connect" | "models">("connect");
  // 新增时先把名称填好（供应商名，重名加序号）：作者不改也能直接往下走。
  const [displayName, setDisplayName] = useState(
    connection?.display_name ?? defaultConnectionName(preset, connections),
  );
  const [baseUrl, setBaseUrl] = useState(connection?.base_url || preset.base_url);
  const [api, setApi] = useState<ProviderApi>(connection?.api ?? preset.api);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>(connection?.models ?? []);
  const [defaultModel, setDefaultModel] = useState(connection?.default_model ?? "");
  const [manual, setManual] = useState("");
  const [discovered, setDiscovered] = useState<string[] | null>(null);
  const [discoveryNote, setDiscoveryNote] = useState<string | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState<"" | "discover" | "save" | "response" | "tool">("");
  const [error, setError] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(preset.custom);

  const testModel = defaultModel || models[0] || "";
  const fingerprint = `${baseUrl.trim()}|${api}|${apiKey.length}|${testModel}`;
  const tested = result && result.fingerprint === fingerprint ? result : null;

  const candidates = useMemo(() => {
    const merged = [...models];
    for (const item of discovered ?? []) if (!merged.includes(item)) merged.push(item);
    return merged;
  }, [models, discovered]);

  const draft = (): ConnectionDraft => ({
    display_name: displayName.trim(),
    catalog_id: preset.id,
    base_url: baseUrl.trim(),
    api,
    models,
    default_model: defaultModel || models[0] || "",
    ...(apiKey ? { api_key: apiKey } : {}),
  });

  const applyRegion = (regionId: string) => {
    const region = preset.regions.find((item) => item.id === regionId);
    if (region) setBaseUrl(region.base_url);
  };

  const toggleModel = (model: string) => {
    setModels((current) => {
      if (current.includes(model)) {
        const next = current.filter((item) => item !== model);
        if (defaultModel === model) setDefaultModel(next[0] ?? "");
        return next;
      }
      if (!defaultModel) setDefaultModel(model);
      return [...current, model];
    });
  };

  const addManual = () => {
    const value = manual.trim();
    if (!value) return;
    setModels((current) => (current.includes(value) ? current : [...current, value]));
    setDefaultModel((current) => current || value);
    setManual("");
  };

  const runDiscover = async () => {
    setBusy("discover");
    setError(null);
    setDiscoveryNote(null);
    try {
      const found = await discoverModels({
        catalog_id: preset.id,
        base_url: baseUrl.trim(),
        api,
        api_key: apiKey || undefined,
        connection_id: connection?.name ?? null,
      });
      setDiscovered(found.models);
      if (found.code) {
        // 认证/网络失败要写清原因，不能显示成「这家没有模型」。
        setDiscoveryNote(found.message);
      } else if (found.models.length === 0) {
        setDiscoveryNote("供应商返回了空列表；可以手动填写模型 ID。");
      } else if (!found.complete) {
        setDiscoveryNote("列表未取完（达到分页上限），可以手动补充。");
      }
    } catch (caught) {
      setDiscoveryNote(describe(caught));
    } finally {
      setBusy("");
    }
  };

  const runProbe = async (kind: "response" | "tool") => {
    if (!testModel) return;
    setBusy(kind);
    setError(null);
    try {
      const probe = await probeModel({
        catalog_id: preset.id,
        base_url: baseUrl.trim(),
        api,
        api_key: apiKey || undefined,
        connection_id: connection?.name ?? null,
        kind,
        model: testModel,
      });
      setResult((current) => ({
        fingerprint,
        response: kind === "response" ? probe.response : (current?.response ?? "untested"),
        toolCall: kind === "tool" ? probe.tool_call : (current?.toolCall ?? "untested"),
        message: probe.message,
      }));
    } catch (caught) {
      setError(describe(caught));
    } finally {
      setBusy("");
    }
  };

  const next = () => {
    if (!baseUrl.trim().startsWith("http")) {
      setError("[MODEL_URL_INVALID] 模型连接：连接地址无效。建议：使用以 http:// 或 https:// 开头的地址。");
      return;
    }
    if (!connection && !apiKey.trim()) {
      setError("[MODEL_KEY_REQUIRED] 模型连接：未填写 API Key。建议：填写供应商提供的 API Key。");
      return;
    }
    setError(null);
    setStage("models");
  };

  const save = async () => {
    if (models.length === 0) {
      setError("[MODEL_REQUIRED] 模型连接：未选择模型。建议：勾选或手动添加至少一个模型。");
      return;
    }
    setBusy("save");
    setError(null);
    const payload = draft();
    const ok = connection ? await update(connection.name, payload) : await create(payload);
    setBusy("");
    if (ok) {
      setApiKey("");
      onDone();
      return;
    }
    setError(useModelsStore.getState().error);
  };

  return (
    <div>
      <div className="flex items-center gap-2">
        <Button className="text-sm text-ui-muted underline" onClick={onBack}>
          ← 换供应商
        </Button>
        <span className="text-sm text-ui-muted">
          {connection ? "编辑连接" : "新增连接"} · {preset.display_name}
        </span>
        <div className="flex-1" />
        <span className="text-sm text-ui-muted">
          {stage === "connect" ? "第 2 步 / 3" : "第 3 步 / 3"}
        </span>
      </div>

      {stage === "connect" ? (
        <div className="mt-3 space-y-3">
          <div>
            <label className="block text-xs font-bold text-[#4b4737]" htmlFor="connection-name">
              连接名称
            </label>
            <input
              id="connection-name"
              autoFocus
              className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <p className="mt-1 text-sm text-ui-muted">
              默认用供应商名称，可以改成「我的 DeepSeek」这类更好认的名字；随时能改。
            </p>
          </div>

          <div>
            <label className="block text-xs font-bold text-[#4b4737]" htmlFor="connection-key">
              API Key
            </label>
            <input
              id="connection-key"
              type="password"
              autoComplete="off"
              className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
              placeholder={connection?.has_key ? `已保存 ${connection.masked_key}，留空沿用` : ""}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
            <p className="mt-1 text-sm text-ui-muted">
              Key 保存在本机系统凭据存储，不写进作品目录。
            </p>
          </div>

          {preset.regions.length > 0 && (
            <div>
              <label className="block text-xs font-bold text-[#4b4737]" htmlFor="connection-region">
                服务区域
              </label>
              <select
                id="connection-region"
                className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-2 py-2 text-sm"
                value={preset.regions.find((region) => region.base_url === baseUrl)?.id ?? ""}
                onChange={(event) => applyRegion(event.target.value)}
              >
                <option value="">（保持不变）</option>
                {preset.regions.map((region) => (
                  <option key={region.id} value={region.id}>
                    {region.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <Button
              className="text-sm text-ui-muted underline"
              onClick={() => setAdvanced((current) => !current)}
            >
              {advanced ? "收起高级设置" : "高级设置（地址与协议）"}
            </Button>
            {advanced && (
              <div className="mt-2 space-y-2 rounded border border-[#e2dbc6] bg-white/60 p-3">
                <div>
                  <label className="block text-sm font-bold text-[#4b4737]" htmlFor="connection-url">
                    基础地址
                  </label>
                  <input
                    id="connection-url"
                    className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-2 py-1.5 text-xs"
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                  />
                  {preset.docs_url && (
                    <a
                      className="mt-1 inline-block text-sm text-[#3a6b8c] underline"
                      href={preset.docs_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      供应商接入文档
                    </a>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-bold text-[#4b4737]" htmlFor="connection-api">
                    接口类型
                  </label>
                  <select
                    id="connection-api"
                    className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-2 py-1.5 text-xs"
                    value={api}
                    onChange={(event) => {
                      setApi(event.target.value as ProviderApi);
                      // 换协议会让已有测试结果失效：结果与协议绑定。
                      setResult(null);
                    }}
                  >
                    {preset.api_options.map((option) => (
                      <option key={option} value={option}>
                        {API_LABEL[option]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {preset.note && <p className="text-sm leading-4 text-ui-muted">{preset.note}</p>}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2">
            <div className="text-xs font-bold text-[#4b4737]">可选模型</div>
            <div className="flex-1" />
            <Button
              className="px-2 py-1 text-sm"
              disabled={busy === "discover"}
              onClick={() => void runDiscover()}
            >
              {busy === "discover" ? "获取中…" : "获取模型列表"}
            </Button>
          </div>

          {discoveryNote && <p className="text-sm leading-4 text-[#8a6d3b]">{discoveryNote}</p>}

          <ul className="max-h-52 space-y-0.5 overflow-auto rounded border border-[#e2dbc6] bg-white/60 p-2">
            {candidates.map((model) => {
              const missing = discovered !== null && !discovered.includes(model);
              return (
                <li key={model}>
                  <label className="flex items-center gap-2 rounded px-1 py-1 text-xs hover:bg-[#f3ecd8]">
                    <input
                      type="checkbox"
                      checked={models.includes(model)}
                      onChange={() => toggleModel(model)}
                    />
                    <span className="min-w-0 flex-1 truncate font-mono">{model}</span>
                    {missing && (
                      <span className="text-sm text-[#a08050]">本次列表未返回</span>
                    )}
                  </label>
                </li>
              );
            })}
            {candidates.length === 0 && (
              <li className="px-1 py-1 text-sm text-ui-muted">
                还没有模型：先获取列表，或在下面手动添加。
              </li>
            )}
          </ul>

          <div className="flex gap-2">
            <input
              aria-label="手动添加模型 ID"
              className="min-w-0 flex-1 rounded border border-[#cfc7ae] bg-white px-2 py-1.5 text-xs"
              placeholder="手动添加模型 ID，例如 deepseek-chat"
              value={manual}
              onChange={(event) => setManual(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addManual();
                }
              }}
            />
            <Button className="px-2 py-1 text-sm" onClick={addManual}>
              添加
            </Button>
          </div>

          <div>
            <label className="block text-xs font-bold text-[#4b4737]" htmlFor="default-model">
              默认模型
            </label>
            <select
              id="default-model"
              className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-2 py-2 text-sm"
              value={defaultModel}
              onChange={(event) => setDefaultModel(event.target.value)}
              disabled={models.length === 0}
            >
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-ui-muted">
              保存连接不会自动切换到它；配好后用「设为当前」或右上角选择器切换。
            </p>
          </div>

          <div className="rounded border border-[#e2dbc6] bg-white/60 p-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-[#4b4737]">连接测试</span>
              <div className="flex-1" />
              <Button
                className="px-2 py-0.5 text-sm"
                disabled={!testModel || busy === "response"}
                onClick={() => void runProbe("response")}
              >
                {busy === "response" ? "测试中…" : "测试响应"}
              </Button>
              <Button
                className="px-2 py-0.5 text-sm"
                disabled={!testModel || busy === "tool"}
                onClick={() => void runProbe("tool")}
              >
                {busy === "tool" ? "测试中…" : "测试工具调用"}
              </Button>
            </div>
            <p className="mt-1 text-sm text-ui-muted">
              测试会向该供应商发出少量请求；不读取作品，也不产生文件改动。
            </p>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
              <dt className="text-ui-muted">响应</dt>
              <dd data-testid="probe-response">{statusText(tested?.response)}</dd>
              <dt className="text-ui-muted">工具调用</dt>
              <dd data-testid="probe-tool">{statusText(tested?.toolCall)}</dd>
            </dl>
            {tested?.message && (
              <p className="mt-1 text-sm leading-4 text-ui-muted">{tested.message}</p>
            )}
            {tested && tested.toolCall !== "passed" && (
              <p className="mt-1 text-sm text-[#a06030]">尚未验证可用于文件操作。</p>
            )}
          </div>
        </div>
      )}

      {(error || conflict) && (
        <div role="alert" className="mt-3 rounded border border-[#c98b8b] bg-[#f7e6e6] px-3 py-2 text-sm text-[#8a3b3b]">
          {conflict ? "[MODEL_CONFIG_CONFLICT] 模型连接：配置已被其他页面或手工编辑改动。建议：保留当前输入，重新读取后再保存。" : error}
          {conflict && (
            <Button
              className="ml-2 underline"
              onClick={() => {
                void refresh();
                onDone();
              }}
            >
              重新读取
            </Button>
          )}
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <Button className="px-3 py-1 text-xs" onClick={onDone}>
          取消
        </Button>
        {stage === "connect" ? (
          <Button variant="primary" className="px-3 py-1 text-xs" onClick={next}>
            下一步：选择模型
          </Button>
        ) : (
          <Button
            variant="primary" className="px-3 py-1 text-xs"
            disabled={busy === "save" || models.length === 0}
            onClick={() => void save()}
          >
            {busy === "save" ? "保存中…" : "保存连接"}
          </Button>
        )}
      </div>
    </div>
  );
}

function statusText(value: "passed" | "failed" | "untested" | undefined): string {
  if (value === "passed") return "通过";
  if (value === "failed") return "未通过";
  return "未测试";
}
