/**
 * 模型设置客户端（0.4 V04-9C）。
 *
 * 对应后端 `hastings/server/model_api.py` 的应用级 `/api/model-*`：供应商预设、
 * 连接增删改、模型发现与测试、当前选择。写操作必须带 `If-Match`（上一次读到的
 * 配置版本），冲突由后端返回 409，界面据此保留表单。
 *
 * 这一层不保存 Key：`api_key` 只在一次请求里出现，进入全局状态的一律是脱敏视图。
 */

export type ProviderApi =
  | "openai-completions"
  | "openai-responses"
  | "anthropic-messages"
  | "google-generative-ai";

export type DiscoveryStrategy = "openai-models" | "anthropic-models" | "google-models";

export interface RegionOption {
  id: string;
  label: string;
  base_url: string;
}

export interface ProviderPreset {
  id: string;
  display_name: string;
  api: ProviderApi;
  api_options: ProviderApi[];
  base_url: string;
  docs_url: string;
  discovery: DiscoveryStrategy | null;
  regions: RegionOption[];
  custom: boolean;
  note: string;
}

export interface ModelConnection {
  name: string;
  display_name: string;
  catalog_id: string;
  api: ProviderApi;
  base_url: string;
  models: string[];
  default_model: string;
  has_key: boolean;
  masked_key: string;
  credential_storage: "system" | "session";
}

export type SelectionSource = "saved" | "startup" | "automatic" | "none";

export interface ModelSelection {
  connection_id: string | null;
  model: string;
  source: SelectionSource;
  available: boolean;
  reason: string;
}

export interface ModelConfig {
  revision: string;
  selection: ModelSelection;
  connections: ModelConnection[];
}

export interface ConnectionDraft {
  display_name: string;
  catalog_id: string;
  base_url: string;
  api: ProviderApi;
  models: string[];
  default_model: string;
  /** 省略表示保留旧 Key；新增时必须非空。 */
  api_key?: string;
}

export interface DiscoveryResult {
  revision: string;
  connection_id: string | null;
  models: string[];
  complete: boolean;
  code: string | null;
  message: string;
}

export interface ProbeResult {
  revision: string;
  connection_id: string | null;
  model: string;
  response: "passed" | "failed" | "untested";
  tool_call: "passed" | "failed" | "untested";
  code: string | null;
  message: string;
}

const BASE = "/api";

export class ModelApiError extends Error {
  constructor(
    readonly code: string,
    readonly why: string,
    readonly fix: string,
    readonly status: number,
    readonly where: string = "模型连接",
  ) {
    super(why);
    this.name = "ModelApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; where?: string; why?: string; fix?: string };
    } | null;
    const error = body?.error;
    throw new ModelApiError(
      error?.code ?? `HTTP_${response.status}`,
      error?.why ?? `请求没有成功（HTTP ${response.status}）`,
      error?.fix ?? "稍后重试；若持续失败，按状态码查后端日志",
      response.status,
      error?.where ?? "模型连接",
    );
  }
  return (await response.json()) as T;
}

export function listProviders(): Promise<ProviderPreset[]> {
  return request<ProviderPreset[]>("/model-providers");
}

export function readConfig(): Promise<ModelConfig> {
  return request<ModelConfig>("/model-connections");
}

export function createConnection(draft: ConnectionDraft, revision: string): Promise<ModelConfig> {
  return request<ModelConfig>("/model-connections", {
    method: "POST",
    headers: { "If-Match": revision },
    body: JSON.stringify(draft),
  });
}

export function updateConnection(
  name: string,
  draft: ConnectionDraft,
  revision: string,
): Promise<ModelConfig> {
  return request<ModelConfig>(`/model-connections/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "If-Match": revision },
    body: JSON.stringify(draft),
  });
}

export function deleteConnection(name: string, revision: string): Promise<ModelConfig> {
  return request<ModelConfig>(`/model-connections/${encodeURIComponent(name)}`, {
    method: "DELETE",
    headers: { "If-Match": revision },
  });
}

export function selectModel(
  connectionId: string | null,
  model: string,
  revision: string,
): Promise<ModelConfig> {
  return request<ModelConfig>("/model-selection", {
    method: "PUT",
    headers: { "If-Match": revision },
    body: JSON.stringify({ connection_id: connectionId, model }),
  });
}

export function discoverModels(
  draft: Pick<ConnectionDraft, "catalog_id" | "base_url" | "api" | "api_key"> & {
    connection_id?: string | null;
  },
): Promise<DiscoveryResult> {
  return request<DiscoveryResult>("/model-connections/discover", {
    method: "POST",
    body: JSON.stringify({
      connection_id: draft.connection_id ?? null,
      catalog_id: draft.catalog_id,
      base_url: draft.base_url,
      api: draft.api,
      api_key: draft.api_key ?? null,
    }),
  });
}

export function probeModel(
  draft: Pick<ConnectionDraft, "catalog_id" | "base_url" | "api" | "api_key"> & {
    connection_id?: string | null;
    kind: "response" | "tool";
    model: string;
  },
): Promise<ProbeResult> {
  return request<ProbeResult>("/model-connections/probe", {
    method: "POST",
    body: JSON.stringify({
      connection_id: draft.connection_id ?? null,
      catalog_id: draft.catalog_id,
      base_url: draft.base_url,
      api: draft.api,
      api_key: draft.api_key ?? null,
      kind: draft.kind,
      model: draft.model,
    }),
  });
}
