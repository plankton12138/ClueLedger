import { useLayoutEffect, useRef, useState } from "react";
import { getNodesBounds, useReactFlow, useViewport, type Node } from "@xyflow/react";

/** 原生滚动条与画布视口共用坐标；拖图、缩放和滚动条不会各自保留一份位置。 */
export default function CanvasScrollbars({ nodes }: { nodes: Node[] }) {
  const horizontal = useRef<HTMLDivElement>(null);
  const vertical = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const viewport = useViewport();
  const flow = useReactFlow();
  const bounds = getNodesBounds(nodes);
  const limits = useRef({ key: "", left: 0, top: 0, right: 0, bottom: 0 });

  useLayoutEffect(() => {
    const host = horizontal.current?.parentElement;
    if (!host) return;
    const update = () => setSize({ width: Math.max(0, host.clientWidth - 18), height: Math.max(0, host.clientHeight - 18) });
    const observer = new ResizeObserver(update);
    observer.observe(host);
    update();
    return () => observer.disconnect();
  }, []);

  // 同一尺寸和缩放下只扩展范围，避免拖滚动条时范围缩小造成滑块跳动。
  // 左侧额外空间包含回收连线；缩小至全貌后不强制放大或重新排布。
  const { zoom, x, y } = viewport;
  const key = `${bounds.x}:${bounds.y}:${bounds.width}:${bounds.height}:${zoom}:${size.width}:${size.height}`;
  const margin = 40;
  const previous = limits.current.key === key ? limits.current : null;
  const range = {
    key,
    left: Math.min(previous?.left ?? Infinity, (bounds.x - 180) * zoom - margin, -x),
    top: Math.min(previous?.top ?? Infinity, bounds.y * zoom - margin, -y),
    right: Math.max(previous?.right ?? -Infinity, (bounds.x + bounds.width) * zoom + margin, size.width - x),
    bottom: Math.max(previous?.bottom ?? -Infinity, (bounds.y + bounds.height) * zoom + margin, size.height - y),
  };
  limits.current = range;

  useLayoutEffect(() => {
    if (horizontal.current) horizontal.current.scrollLeft = -x - range.left;
    if (vertical.current) vertical.current.scrollTop = -y - range.top;
  }, [x, y, range.left, range.top, range.right, range.bottom, size]);

  return <>
    <div ref={horizontal} className="story-scrollbar story-scrollbar-x" role="region" aria-label="画布横向滚动" tabIndex={0}
      onScroll={event => {
        const current = flow.getViewport();
        const next = -limits.current.left - event.currentTarget.scrollLeft;
        // 同步视口触发的原生 scroll 事件不再回写，保留亚像素位置，避免反馈循环。
        if (Math.abs(next - current.x) > 1) void flow.setViewport({ ...current, x: next });
      }}>
      <div style={{ width: Math.ceil(range.right - range.left), height: 1 }} />
    </div>
    <div ref={vertical} className="story-scrollbar story-scrollbar-y" role="region" aria-label="画布纵向滚动" tabIndex={0}
      onScroll={event => {
        const current = flow.getViewport();
        const next = -limits.current.top - event.currentTarget.scrollTop;
        if (Math.abs(next - current.y) > 1) void flow.setViewport({ ...current, y: next });
      }}>
      <div style={{ height: Math.ceil(range.bottom - range.top), width: 1 }} />
    </div>
    <div className="story-scrollbar-corner" aria-hidden="true" />
  </>;
}
