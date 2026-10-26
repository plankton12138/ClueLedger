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

/** 一条连接怎么证明「我是这个账号」：自带 API Key，或登录订阅账号。 */
export type AuthMethod = "api_key" | "subscription";

/** 凭据状态：可调用 / 需要续期 / 没有凭据。 */
export type AuthStatus = "ready" | "expired" | "missing";

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
  /** 该供应商支持的订阅登录来源；空表示只能填 API Key。 */
  auth_providers: string[];
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
  /** 凭据是否就绪：API Key 与订阅登录共用同一个可用性判断。 */
  has_key: boolean;
  masked_key: string;
  credential_storage: "system" | "session";
  auth_method: AuthMethod;
  /** 订阅来源标识（如 chatgpt）；API Key 连接为空串。 */
  auth_provider: string;
  /** 订阅账号的脱敏标识；API Key 连接为空串。 */
  account: string;
  auth_status: AuthStatus;
}

export type SelectionSource = "saved" | "startup" | "automatic" | "none";

export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface ModelSelection {
  connection_id: string | null;
  model: string;
  source: SelectionSource;
  available: boolean;
  reason: string;
  thinking_level: ThinkingLevel | null;
  thinking_levels: ThinkingLevel[];
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
  /** 省略表示保留旧 Key；新增 API Key 连接时必须非空。 */
  api_key?: string;
  auth_method?: AuthMethod;
  /** 订阅来源标识（如 chatgpt）。 */
  auth_provider?: string;
  /** 一次已完成的授权会话；保存成功后凭据归属这个连接。 */
  auth_session_id?: string;
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

/** 一家可以「登录订阅账号」的来源；与 API Key 预设是两件事。 */
export interface AuthProvider {
  id: string;
  name: string;
}

export type AuthSessionStatus = "pending" | "succeeded" | "failed" | "cancelled" | "expired";

/** 授权会话：只有进度与账号标识，没有任何令牌。 */
export interface AuthSession {
  id: string;
  provider_id: string;
  provider_name: string;
  status: AuthSessionStatus;
  auth_url: string;
  user_code: string;
  instructions: string;
  expires_at: number;
  account: string;
  code: string;
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
  thinkingLevel?: ThinkingLevel | null,
): Promise<ModelConfig> {
  return request<ModelConfig>("/model-selection", {
    method: "PUT",
    headers: { "If-Match": revision },
    body: JSON.stringify({ connection_id: connectionId, model, thinking_level: thinkingLevel }),
  });
}

export function discoverModels(
  draft: Pick<
    ConnectionDraft,
    "catalog_id" | "base_url" | "api" | "api_key" | "auth_provider" | "auth_session_id"
  > & {
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
      auth_provider: draft.auth_provider ?? "",
      auth_session_id: draft.auth_session_id ?? "",
    }),
  });
}

export function probeModel(
  draft: Pick<
    ConnectionDraft,
    "catalog_id" | "base_url" | "api" | "api_key" | "auth_provider" | "auth_session_id"
  > & {
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
      auth_provider: draft.auth_provider ?? "",
      auth_session_id: draft.auth_session_id ?? "",
      kind: draft.kind,
      model: draft.model,
    }),
  });
}

export function listAuthProviders(): Promise<AuthProvider[]> {
  return request<AuthProvider[]>("/model-auth/providers");
}

export function startAuthSession(providerId: string): Promise<AuthSession> {
  return request<AuthSession>("/model-auth/sessions", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId }),
  });
}

export function readAuthSession(sessionId: string): Promise<AuthSession> {
  return request<AuthSession>(`/model-auth/sessions/${encodeURIComponent(sessionId)}`);
}

export function cancelAuthSession(sessionId: string): Promise<AuthSession> {
  return request<AuthSession>(`/model-auth/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}
