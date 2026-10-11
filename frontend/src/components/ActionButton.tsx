/**
 * 对话区可点选动作按钮（P9 D5）：空态建议（T3，本地 stage 映射）与
 * 选项按钮（T4，模型输出解析）共用同一视觉——来源不同，外观一体。
 * UI 基建改造后为 ui/Button 的 soft 变体封装，调用面保持不变。
 */

import { Button } from "@/components/ui/button";

export default function ActionButton({
  label,
  onClick,
  title,
  disabled = false,
  active = false,
}: {
  label: string;
  onClick: () => void;
  /** 悬停补充（选项按钮用来显示取舍说明）。 */
  title?: string;
  disabled?: boolean;
  /** 选中态（P10 D10 多维度组内单选）：实心高亮以示当前选择。 */
  active?: boolean;
}) {
  return (
    <Button
      variant={active ? "default" : "soft"}
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="h-auto px-3 py-1.5 text-xs"
    >
      {label}
    </Button>
  );
}
