/**
 * 栏宽拖拽把手（0.4 V04-11）。
 *
 * 两侧栏之间的窄条：按住左右拖动改宽度，双击恢复默认。用 pointer 事件而不是鼠标事件，
 * 触摸与手写笔走同一条路径；拖动期间在 window 上监听，指针移出把手也不会中断。
 *
 * 右侧对话栏的把手方向相反（指针左移 = 变宽），由 `invert` 表达，避免为对称布局写两份逻辑。
 */

import type { PointerEvent as ReactPointerEvent } from "react";

interface Props {
  value: number;
  min: number;
  max: number;
  invert?: boolean;
  label: string;
  onChange: (width: number) => void;
  onReset: () => void;
}

export default function ResizeHandle({
  value,
  min,
  max,
  invert = false,
  label,
  onChange,
  onReset,
}: Props) {
  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startValue = value;
    const onMove = (move: PointerEvent) => {
      const delta = (move.clientX - startX) * (invert ? -1 : 1);
      onChange(Math.min(max, Math.max(min, startValue + delta)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className="resize-handle"
      title="拖动调整宽度，双击恢复默认"
      onPointerDown={startDrag}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        // 键盘微调：方向键每次 16px，Home 恢复默认
        const step = event.shiftKey ? 48 : 16;
        const grow = invert ? "ArrowLeft" : "ArrowRight";
        const shrink = invert ? "ArrowRight" : "ArrowLeft";
        if (event.key === grow) {
          event.preventDefault();
          onChange(Math.min(max, value + step));
        } else if (event.key === shrink) {
          event.preventDefault();
          onChange(Math.max(min, value - step));
        } else if (event.key === "Home") {
          event.preventDefault();
          onReset();
        }
      }}
    />
  );
}
