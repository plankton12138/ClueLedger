/**
 * 模型设置客户端（0.4 V04-9C）。
 *
 * 对应后端 `hastings/server/model_api.py` 的应用级 `/api/model-*`：供应商预设、
 * 连接增删改、模型发现与测试、当前选择。写操作必须带 `If-Match`（上一次读到的
 * 配置版本），冲突由后端返回 409，界面据此保留表单。
 *
 * 这一层不保存 Key：`api_key` 只在一次请求里出现，进入全局状态的一律是脱敏视图。
 */

import { readOperation, readOperationResult } from "./operations";

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
  /** 按精确模型名保存的容量；未出现的模型按未知处理，界面不显示百分比。 */
  model_limits: Record<string, ModelLimitInput>;
  /** 服务商在模型列表里返回的容量；与作者手填的容量分开。 */
  model_metadata: Record<string, ModelCapacityInput>;
}

/** 供应商返回的容量；只含三个上限，不含线簿的运行时预留。 */
export interface ModelCapacityInput {
  context_window_tokens?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
}

/** 一个模型的容量；只填知道的项，空缺表示未知。 */
export interface ModelLimitInput {
  context_window_tokens?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  output_reserve_tokens?: number;
  safety_tokens?: number;
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
  /** 供应商在列表里明确给出的容量；只有名字的供应商这里是空映射。 */
  limits: Record<string, ModelCapacityInput>;
  complete: boolean;
  code: string | null;
  message: string;
}

export interface ProbeResult {
  model: string;
  response: "passed" | "failed" | "untested";
  tool_call: "passed" | "failed" | "untested";
  code: string | null;
  message: string;
}

/** 受理结果：测试作为统一操作在后台跑，结果按操作读取。 */
export interface ProbeAccepted {
  revision: string;
  connection_id: string | null;
  operation_id: string;
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
    /** 同一次点击重试沿用同一个键，服务端据此去重。 */
    requestKey?: string;
  },
): Promise<ProbeAccepted> {
  return request<ProbeAccepted>("/model-connections/probe", {
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
      request_key: draft.requestKey ?? "",
    }),
  });
}

/** 测试轮询间隔与上限：一次点击必须有终点，不能无限转圈。 */
const PROBE_POLL_MS = 400;
const PROBE_DEADLINE_MS = 120_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 跟进一次连接测试：按操作快照读状态，结束后从结果收据还原两条独立事实。
 *
 * 暂停会被明确报告为「没有采用这次结果」，不会把取消说成失败或通过。
 */
export async function awaitProbe(
  operationId: string,
  kind: "response" | "tool",
): Promise<ProbeResult> {
  const deadline = Date.now() + PROBE_DEADLINE_MS;
  while (Date.now() < deadline) {
    const operation = await readOperation(operationId);
    if (operation.status === "completed" || operation.status === "failed") {
      const result = await readOperationResult(operationId);
      const outcome = result.receipts.find((item) => item.kind === "probe")?.outcome;
      return {
        model: outcome?.model || operation.model,
        response: outcome?.response ?? (kind === "response" ? "failed" : "untested"),
        tool_call: outcome?.tool_call ?? (kind === "tool" ? "failed" : "untested"),
        code: outcome?.code ?? operation.error?.code ?? null,
        message: outcome?.message || operation.note,
      };
    }
    if (operation.status === "paused") {
      throw new ModelApiError(
        "MODEL_PROBE_PAUSED",
        "连接测试已暂停；没有采用这次结果。",
        "重新测试，或先保存连接再测试",
        409,
        "模型连接",
      );
    }
    await sleep(PROBE_POLL_MS);
  }
  throw new ModelApiError(
    "MODEL_PROBE_TIMEOUT",
    "连接测试没有在规定时间内结束。",
    "稍后重试；若持续失败，检查网络与供应商状态",
    504,
    "模型连接",
  );
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
