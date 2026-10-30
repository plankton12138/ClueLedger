/** 悬浮位置以可视区域中的右下角比例保存，拖动可越过窗口边缘，但始终保留标题栏可抓取。 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { DEFAULT_IDEA_POSITION, MAX_POSITION_RATIO, useIdeasSurfaceStore, type IdeaPosition } from "../../state/ideasSurface";

export interface FloatingBounds { left: number; top: number; right: number; bottom: number }
export interface FloatingSize { width: number; height: number }
/** 拖出窗口边缘时至少留在可视区内的宽度或高度，保证标题栏仍可按住拖回。 */
export const FLOATING_KEEP = 48;

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)));
const keepOf = (size: number) => Math.min(FLOATING_KEEP, size);

export function floatingPoint(bounds: FloatingBounds, size: FloatingSize, position: IdeaPosition) {
  const keepX = keepOf(size.width);
  const keepY = keepOf(size.height);
  const left = position.x > 1
    ? bounds.right - size.width + (position.x - 1) * Math.max(1, size.width - keepX)
    : bounds.left + (bounds.right - bounds.left) * position.x - size.width;
  const top = position.y > 1
    ? bounds.bottom - size.height + (position.y - 1) * Math.max(1, size.height - keepY)
    : bounds.top + (bounds.bottom - bounds.top) * position.y - size.height;
  return {
    left: clamp(left, bounds.left, bounds.right - keepX),
    top: clamp(top, bounds.top, bounds.bottom - keepY),
  };
}

export function floatingAnchor(bounds: FloatingBounds, size: FloatingSize, point: { left: number; top: number }): IdeaPosition {
  const keepX = keepOf(size.width);
  const keepY = keepOf(size.height);
  const fitRight = bounds.right - size.width;
  const fitBottom = bounds.bottom - size.height;
  const x = point.left > fitRight
    ? 1 + (point.left - fitRight) / Math.max(1, size.width - keepX)
    : (point.left + size.width - bounds.left) / Math.max(1, bounds.right - bounds.left);
  const y = point.top > fitBottom
    ? 1 + (point.top - fitBottom) / Math.max(1, size.height - keepY)
    : (point.top + size.height - bounds.top) / Math.max(1, bounds.bottom - bounds.top);
  return {
    x: clamp(x, 0, MAX_POSITION_RATIO),
    y: clamp(y, 0, MAX_POSITION_RATIO),
  };
}

function viewportBounds(): FloatingBounds {
  const view = window.visualViewport;
  const left = (view?.offsetLeft ?? 0) + 10;
  const right = (view?.offsetLeft ?? 0) + (view?.width ?? window.innerWidth) - 10;
  const top = (view?.offsetTop ?? 0) + 10;
  const bottom = (view?.offsetTop ?? 0) + (view?.height ?? window.innerHeight) - 10;
  return { left, top, right, bottom };
}

export function useIdeasPosition(element: RefObject<HTMLElement>, open: boolean, workspaceId: string | null) {
  const saved = useIdeasSurfaceStore(state => state.position);
  const [moving, setMoving] = useState<IdeaPosition | null>(null);
  const [geometry, setGeometry] = useState(() => ({ bounds: viewportBounds(), size: { width: 82, height: 44 }, headerBottom: 0 }));
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const naturalPoint = floatingPoint(geometry.bounds, geometry.size, moving ?? saved);
  const defaultPlacement = saved.x === DEFAULT_IDEA_POSITION.x && saved.y === DEFAULT_IDEA_POSITION.y;
  // 仅调整默认展开位置；作者拖动后的位置仍可以越过顶栏。
  const point = open && defaultPlacement && !moving
    ? { ...naturalPoint, top: Math.max(naturalPoint.top, geometry.headerBottom + 10) }
    : naturalPoint;
  const lastPoint = useRef(point);
  lastPoint.current = point;

  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const measure = () => {
      const bounds = viewportBounds();
      const headerBottom = document.querySelector<HTMLElement>(".app-header")?.getBoundingClientRect().bottom ?? 0;
      const top = open && defaultPlacement ? Math.max(bounds.top, headerBottom + 10) : bounds.top;
      node.style.maxHeight = `${Math.max(44, bounds.bottom - top)}px`;
      node.style.maxWidth = `${Math.max(44, bounds.right - bounds.left)}px`;
      setGeometry({ bounds, size: { width: node.offsetWidth, height: node.offsetHeight }, headerBottom });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    const header = document.querySelector<HTMLElement>(".app-header");
    if (header) observer.observe(header);
    window.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("scroll", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("scroll", measure);
    };
  }, [element, open, workspaceId, defaultPlacement]);

  function finish(event: PointerEvent<HTMLElement>, cancelled = false) {
    if (drag.current?.id !== event.pointerId) return;
    if (drag.current.moved) {
      suppressClick.current = !cancelled;
      useIdeasSurfaceStore.getState().setPosition(floatingAnchor(geometry.bounds, geometry.size, lastPoint.current));
    }
    drag.current = null;
    setMoving(null);
  }

  return {
    style: { left: point.left, top: point.top },
    consumeDragClick() {
      const value = suppressClick.current;
      suppressClick.current = false;
      return value;
    },
    handlers: {
      onPointerDown(event: PointerEvent<HTMLElement>) {
        if (event.button !== 0 || !event.isPrimary) return;
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, ...lastPoint.current, moved: false };
        suppressClick.current = false;
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove(event: PointerEvent<HTMLElement>) {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        const dx = event.clientX - current.x, dy = event.clientY - current.y;
        if (!current.moved && Math.hypot(dx, dy) < 6) return;
        current.moved = true;
        const next = floatingPoint(geometry.bounds, geometry.size, floatingAnchor(geometry.bounds, geometry.size, { left: current.left + dx, top: current.top + dy }));
        // 在状态渲染前放开指针时仍保存最新位置。
        lastPoint.current = next;
        setMoving(floatingAnchor(geometry.bounds, geometry.size, next));
      },
      onPointerUp: (event: PointerEvent<HTMLElement>) => finish(event),
      onPointerCancel: (event: PointerEvent<HTMLElement>) => finish(event, true),
      onLostPointerCapture: (event: PointerEvent<HTMLElement>) => finish(event, true),
      onKeyDown(event: KeyboardEvent<HTMLElement>) {
        if (event.nativeEvent.isComposing || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Home") { useIdeasSurfaceStore.getState().resetPosition(); return; }
        const step = event.shiftKey ? 48 : 16;
        const next = { left: point.left + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0), top: point.top + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0) };
        useIdeasSurfaceStore.getState().setPosition(floatingAnchor(geometry.bounds, geometry.size, next));
      },
    },
  };
}
