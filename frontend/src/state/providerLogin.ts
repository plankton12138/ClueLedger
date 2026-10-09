/**
 * 登录式模型接入的纯函数（P7c T16）：目录卡片 → 表单预填。
 *
 * 对齐 tau_coding `/login` 的体验内核——用户点选 provider 后表单应有合理初值，
 * 只需贴 key。重名建议后缀避免静默覆盖既有档案（PUT 是 upsert，重名即修改）。
 */

import type { CatalogEntry } from "../api/rest";

/** 登录表单初值形状（与 SettingsPanel 的表单 state 同构）。 */
export interface ProviderLoginForm {
  name: string;
  base_url: string;
  api_key: string;
  api_key_env: string;
  default_model: string;
}

/**
 * 目录名与已有档案重名时建议带序后缀（deepseek → deepseek-2）；
 * base 本身可用或序号用尽返回 null 之外的值——恒返回非空建议名。
 */
export function suggestProfileName(base: string, existing: string[]): string {
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}-${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** 目录条目 → 登录表单初值：预填可编辑字段，key 恒空起填（明文不可回显）。 */
export function prefillLoginForm(
  entry: CatalogEntry,
  existingNames: string[],
): ProviderLoginForm {
  return {
    name: suggestProfileName(entry.name, existingNames),
    base_url: entry.base_url,
    api_key: "",
    api_key_env: entry.api_key_env,
    default_model: entry.default_model,
  };
}

/** 自定义 provider：全空表单（原「新建档案」路径，D5 不丢能力）。 */
export function emptyLoginForm(): ProviderLoginForm {
  return { name: "", base_url: "", api_key: "", api_key_env: "", default_model: "" };
}

/** base_url → 域名展示（目录卡片副标题）；解析失败原样返回。 */
export function hostOf(base_url: string): string {
  try {
    return new URL(base_url).host;
  } catch {
    return base_url;
  }
}
