/** 悬浮位置以可用区域中的右下角比例保存，缩放和窗口变化只钳制显示位置。 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { useIdeasSurfaceStore, type IdeaPosition } from "../../state/ideasSurface";

export interface FloatingBounds { left: number; top: number; right: number; bottom: number }
export interface FloatingSize { width: number; height: number }
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)));

export function floatingPoint(bounds: FloatingBounds, size: FloatingSize, position: IdeaPosition) {
  return {
    left: clamp(bounds.left + (bounds.right - bounds.left) * position.x - size.width, bounds.left, bounds.right - size.width),
    top: clamp(bounds.top + (bounds.bottom - bounds.top) * position.y - size.height, bounds.top, bounds.bottom - size.height),
  };
}

export function floatingAnchor(bounds: FloatingBounds, size: FloatingSize, point: { left: number; top: number }): IdeaPosition {
  return {
    x: clamp((point.left + size.width - bounds.left) / Math.max(1, bounds.right - bounds.left), 0, 1),
    y: clamp((point.top + size.height - bounds.top) / Math.max(1, bounds.bottom - bounds.top), 0, 1),
  };
}

function viewportBounds(): FloatingBounds {
  const view = window.visualViewport;
  const left = (view?.offsetLeft ?? 0) + 10;
  const right = (view?.offsetLeft ?? 0) + (view?.width ?? window.innerWidth) - 10;
  const bottom = (view?.offsetTop ?? 0) + (view?.height ?? window.innerHeight) - 10;
  const header = document.querySelector<HTMLElement>(".app-header")?.getBoundingClientRect().bottom ?? 60;
  return { left, right, bottom, top: Math.min(Math.max(header + 10, (view?.offsetTop ?? 0) + 10), bottom - 44) };
}

export function useIdeasPosition(element: RefObject<HTMLElement>, open: boolean, workspaceId: string | null) {
  const saved = useIdeasSurfaceStore(state => state.position);
  const [moving, setMoving] = useState<IdeaPosition | null>(null);
  const [geometry, setGeometry] = useState(() => ({ bounds: viewportBounds(), size: { width: 82, height: 44 } }));
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const point = floatingPoint(geometry.bounds, geometry.size, moving ?? saved);
  const lastPoint = useRef(point);
  lastPoint.current = point;

  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const measure = () => {
      const bounds = viewportBounds();
      node.style.maxHeight = `${Math.max(44, bounds.bottom - bounds.top)}px`;
      node.style.maxWidth = `${Math.max(44, bounds.right - bounds.left)}px`;
      setGeometry({ bounds, size: { width: node.offsetWidth, height: node.offsetHeight } });
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
  }, [element, open, workspaceId]);

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
        useIdeasSurfaceStore.getState().setPosition(floatingAnchor(geometry.bounds, geometry.size, floatingPoint(geometry.bounds, geometry.size, floatingAnchor(geometry.bounds, geometry.size, next))));
      },
    },
  };
}
