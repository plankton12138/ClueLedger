/**
 * 本轮材料（0.6 V06-A2）。
 *
 * 说明应用实际提供了什么：指定了哪些文件、提供了全文还是部分原文、还差多少字符。
 * 它不证明模型理解或关注了内容；「已提供」只是传输范围的事实。
 */

import { materialComplete, materialReadChars, type TurnMaterial } from "../../state/chatMessages";

interface Props {
  materials: TurnMaterial[];
}

function status(material: TurnMaterial): string {
  const read = materialReadChars(material);
  if (material.length === 0) return "空文件";
  if (materialComplete(material)) return "已提供全文";
  if (read === 0) return "已指定，尚未读取";
  return `已提供部分原文（${read}/${material.length} 字符）`;
}

export default function TurnMaterials({ materials }: Props) {
  if (materials.length === 0) return null;
  return (
    <details className="mt-1 text-[11px] text-sub" data-testid="turn-materials">
      <summary className="cursor-pointer text-[10px] text-mute">
        本轮材料 {materials.length} 份
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        {materials.map((material) => (
          <li key={material.path} className="flex flex-wrap items-center gap-1">
            <span className="min-w-0 flex-1 truncate">{material.path}</span>
            {material.required && <span className="text-[10px] text-amber">必读</span>}
            <span className="text-[10px] text-mute">{status(material)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
