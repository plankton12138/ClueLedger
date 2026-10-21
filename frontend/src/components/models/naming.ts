/**
 * 连接默认名称（0.4 V04-9 补充）。
 *
 * 新增连接时先给一个能直接用的名字，作者不必先想名字再填 Key；想改随时改。
 * 默认取供应商显示名（例如「DeepSeek」），已经有一个同名连接时按序号往后排，
 * 避免两个连接在列表里看起来完全一样。
 */

import type { ModelConnection, ProviderPreset } from "../../api/models";

type PresetLike = Pick<ProviderPreset, "display_name" | "custom">;

export function defaultConnectionName(
  preset: PresetLike,
  existing: Pick<ModelConnection, "display_name">[],
): string {
  const base = preset.display_name.trim() || (preset.custom ? "自定义服务" : "新连接");
  const taken = new Set(existing.map((item) => item.display_name.trim()));
  if (!taken.has(base)) return base;
  let index = 2;
  while (taken.has(`${base} ${index}`)) index += 1;
  return `${base} ${index}`;
}
