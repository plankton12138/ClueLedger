import { Button } from "../ui/Button";
/**
 * 供应商选择（0.4 V04-9C 第一步）。
 *
 * 列表来自后端目录（`/api/model-providers`），前端不复制一份供应商表：两处表就会
 * 各自漂移，而地址写错的表现是「保存成功、调用失败」。
 *
 * 搜索只按显示名与 ID 过滤，不发请求；「自定义服务」固定排在最后，地址留空由作者填。
 */

import { useMemo, useState } from "react";

import type { ProviderPreset } from "../../api/models";

interface Props {
  presets: ProviderPreset[];
  onPick(preset: ProviderPreset): void;
}

export default function ProviderPicker({ presets, onPick }: Props) {
  const [query, setQuery] = useState("");

  const matched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return presets;
    return presets.filter(
      (item) =>
        item.display_name.toLowerCase().includes(needle) ||
        item.id.toLowerCase().includes(needle),
    );
  }, [presets, query]);

  return (
    <div>
      <label className="block text-xs font-bold text-[#4b4737]" htmlFor="provider-search">
        选择供应商
      </label>
      <input
        id="provider-search"
        aria-label="搜索供应商"
        autoFocus
        className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
        placeholder="搜索，例如 DeepSeek"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      <ul className="mt-3 grid max-h-72 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2">
        {matched.map((item) => (
          <li key={item.id}>
            <Button
              className="w-full rounded border border-[#e2dbc6] bg-white/70 px-3 py-2 text-left hover:bg-[#f3ecd8]"
              onClick={() => onPick(item)}
            >
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-xs font-bold text-[#3f3a2c]">
                  {item.display_name}
                </span>
                {item.custom && (
                  <span className="rounded bg-[#e8e0c8] px-1.5 py-0.5 text-sm text-ui-muted">
                    手动
                  </span>
                )}
              </div>
              <div className="mt-0.5 truncate text-sm text-ui-muted">
                {item.custom ? "填写完整地址并选择协议" : item.base_url}
              </div>
            </Button>
          </li>
        ))}
        {matched.length === 0 && (
          <li className="px-2 py-1 text-xs text-ui-muted">没有匹配的供应商。</li>
        )}
      </ul>
    </div>
  );
}
