import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useConversationsStore } from "../../state/conversations";
import { useLayoutStore } from "../../state/layout";
import type { TextReference } from "../../state/textReferences";
import { selectionSourceRange } from "./SourceAnchors";

type Source = Omit<TextReference, "id" | "text">;

export interface MarkSelection {
  text: string;
  range: { start: number; end: number; quote: string } | null;
}

/** 只接受完整落在当前阅读区域内的选段，不捕获工具栏、输入框或其他栏的文字。 */
export default function SelectionToChat({
  source,
  children,
  onMark,
  sourceText = "",
}: {
  source: Source;
  children: ReactNode;
  /** 有源码坐标时才提供「标记线索」；没有锚点时只保留加入对话。 */
  onMark?: (selection: MarkSelection) => void;
  /** 规范化后的 Markdown 源文本，用于把选区换算成码点区间。 */
  sourceText?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const action = useRef<HTMLDivElement>(null);

  const [selection, setSelection] = useState<{ text: string; x: number; y: number; range: { start: number; end: number; quote: string } | null } | null>(null);
  const identity = JSON.stringify(source);

  useEffect(() => {
    setSelection(null);
    const read = () => {
      const selected = window.getSelection();
      if (!selected || selected.isCollapsed || !selected.rangeCount || !root.current) {
        setSelection(null); return;
      }
      const range = selected.getRangeAt(0);
      if (!root.current.contains(range.startContainer) || !root.current.contains(range.endContainer)) {
        setSelection(null); return;
      }
      const text = selected.toString();
      if (!text.trim()) { setSelection(null); return; }
      const rect = range.getBoundingClientRect();
      const mapped = onMark
        ? selectionSourceRange(root.current.querySelector<HTMLElement>(".prose-paper") ?? root.current, sourceText)
        : null;
      setSelection({ text, x: Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - 260)),
        y: rect.top >= 44 ? rect.top - 40 : Math.min(rect.bottom + 6, window.innerHeight - 40), range: mapped });
    };
    const dismiss = () => setSelection(null);
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") dismiss(); };
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target) && !action.current?.contains(event.target)) dismiss();
    };
    document.addEventListener("selectionchange", read);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("selectionchange", read);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [identity, onMark, sourceText]);

  return <div ref={root} data-text-selection-source={source.source}>
    {children}
    {selection && createPortal(<div ref={action} className="selection-actions" role="group" aria-label="选段操作"
      style={{ left: selection.x, top: selection.y }} onMouseDown={event => event.preventDefault()}>
      <button type="button" className="selection-to-chat"
        onClick={() => {
          const added = useConversationsStore.getState().addTextReference({ ...source, id: crypto.randomUUID(), text: selection.text });
          if (added) {
            window.getSelection()?.removeAllRanges();
            setSelection(null);
          }
          useLayoutStore.getState().revealChat();
        }}>添加到对话</button>
      {onMark && selection.range ? (
        <button type="button" className="selection-to-chat"
          onClick={() => {
            onMark({ text: selection.text, range: selection.range });
            window.getSelection()?.removeAllRanges();
            setSelection(null);
          }}>标记线索</button>
      ) : null}
    </div>, document.body)}
  </div>;
}
