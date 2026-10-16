import type { ProviderSummary } from "./rest";
import { MODEL_BACKEND_MISMATCH } from "./compatibility";

export interface ModelSelection {
  provider: string | null;
  model: string;
  display_name: string;
  available: boolean;
}
async function request<T>(
  url: string,
  body?: object,
  method = "POST",
): Promise<T> {
  const res = await fetch(
    url,
    body
      ? {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await res.json().catch(() => null);
  if (url === "/api/model-selection" && [404, 405].includes(res.status))
    throw new Error(MODEL_BACKEND_MISMATCH);
  if (!res.ok)
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `请求失败（HTTP ${res.status}）`,
    );
  return data as T;
}
export const fetchModelSelection = () =>
  request<ModelSelection>("/api/model-selection");
export const selectModel = (provider: string, model: string) =>
  request<ModelSelection>("/api/model-selection", { provider, model }, "PUT");
export const connectProvider = (
  catalog_id: string,
  api_key: string,
  use_environment = false,
) =>
  request<ProviderSummary>("/api/providers/connect", {
    catalog_id,
    api_key,
    use_environment,
  });
export const fetchModelChoices = (name: string) =>
  request<{
    models: string[];
    source: "remote" | "catalog";
    verified: boolean;
    notice: string;
  }>(`/api/providers/${encodeURIComponent(name)}/model-choices`);
