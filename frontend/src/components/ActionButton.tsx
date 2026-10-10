/**
 * 对话区可点选动作按钮（P9 D5）：空态建议（T3，本地 stage 映射）与
 * 选项按钮（T4，模型输出解析）共用同一视觉——来源不同，外观一体。
 */

export default function ActionButton({
  label,
  onClick,
  title,
  disabled = false,
}: {
  label: string;
  onClick: () => void;
  /** 悬停补充（选项按钮用来显示取舍说明）。 */
  title?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="shrink-0 border border-gold/50 bg-gold/10 px-3 py-1.5 text-xs text-gold transition-colors hover:bg-gold/20 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );
}
