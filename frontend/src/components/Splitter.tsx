/**
 * 可调侧栏分隔条（P6 T6，计划 D6）：零依赖 Splitter。
 * pointer events 拖拽（setPointerCapture 指针出窗不丢）、min/max 钳制、
 * 双击重置默认宽、拖拽结束写 localStorage（key 按面板命名，读档损坏回落默认）。
 * 钳制/方向/读档解析均为导出纯函数，vitest 直接覆盖（项目测试基建为
 * node 环境纯函数剧本，DOM 接线人工浏览器验收）。
 */

import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

export const SPLIT_MIN = 220;
export const SPLIT_MAX = 480;

/** 宽度钳制：拖拽与读档共用同一条边界规则；NaN 回落下限，±Infinity 自然钳到边界。 */
export function clampWidth(
  width: number,
  min: number = SPLIT_MIN,
  max: number = SPLIT_MAX,
): number {
  if (Number.isNaN(width)) return min;
  return Math.min(max, Math.max(min, width));
}

/** 拖拽几何：左栏右缘向右拖=变宽；右栏左缘向右拖=变窄（方向相反）。 */
export function dragWidth(
  startWidth: number,
  deltaPx: number,
  side: "left" | "right",
): number {
  return side === "left" ? startWidth + deltaPx : startWidth - deltaPx;
}

/** 读档解析：null/空串/非数值回落默认，越界值钳制——localStorage 可被手改，不信任。 */
export function parseStoredWidth(
  raw: string | null,
  fallback: number,
  min: number = SPLIT_MIN,
  max: number = SPLIT_MAX,
): number {
  if (raw === null || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return clampWidth(parsed, min, max);
}

/** localStorage 访问均带泄压阀：隐私模式等不可用场景退化为会话内调宽。 */
export function loadStoredWidth(key: string, fallback: number): number {
  try {
    return parseStoredWidth(localStorage.getItem(key), fallback);
  } catch {
    return fallback;
  }
}

export function storeWidth(key: string, width: number): void {
  try {
    localStorage.setItem(key, String(clampWidth(width)));
  } catch {
    // 持久化失败不影响本次交互
  }
}

export function clearStoredWidth(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // 同上
  }
}

interface SplitterProps {
  /** 被调宽面板在分隔条哪一侧：left=分隔条在面板右缘，right=在面板左缘。 */
  side: "left" | "right";
  width: number;
  onWidthChange: (width: number) => void;
  /** 双击重置的目标宽度。 */
  defaultWidth: number;
  storageKey: string;
  min?: number;
  max?: number;
  disabled?: boolean;
  className?: string;
}

export default function Splitter({
  side,
  width,
  onWidthChange,
  defaultWidth,
  storageKey,
  min = SPLIT_MIN,
  max = SPLIT_MAX,
  disabled = false,
  className = "",
}: SplitterProps) {
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);
  const widthRef = useRef(width);
  widthRef.current = width;
  const [active, setActive] = useState(false);

  // 拖拽期间锁定全局光标与选区；结束/卸载由 effect 清理兜底。
  useEffect(() => {
    if (!active) return;
    const prevCursor = document.body.style.cursor;
    const prevSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevSelect;
    };
  }, [active]);

  function handlePointerDown(e: ReactPointerEvent<HTMLDivElement>): void {
    if (disabled || e.button !== 0) return;
    drag.current = { startX: e.clientX, startWidth: widthRef.current };
    setActive(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>): void {
    const start = drag.current;
    if (start === null) return;
    onWidthChange(
      clampWidth(dragWidth(start.startWidth, e.clientX - start.startX, side), min, max),
    );
  }

  function endDrag(e: ReactPointerEvent<HTMLDivElement>): void {
    if (drag.current === null) return;
    drag.current = null;
    setActive(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    storeWidth(storageKey, widthRef.current);
  }

  function handleDoubleClick(): void {
    onWidthChange(defaultWidth);
    clearStoredWidth(storageKey);
  }

  return (
    <div
      role="separator"
      aria-label={`调整${side === "left" ? "对话" : "账本"}栏宽度`}
      title="拖拽调宽，双击重置"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={handleDoubleClick}
      className={`group w-1.5 flex-none cursor-col-resize touch-none select-none ${
        disabled ? "pointer-events-none" : ""
      } ${className}`}
    >
      <div
        className={`mx-auto h-full w-px transition-colors ${
          active ? "bg-gold" : "bg-line group-hover:bg-gold/60"
        }`}
      />
    </div>
  );
}
