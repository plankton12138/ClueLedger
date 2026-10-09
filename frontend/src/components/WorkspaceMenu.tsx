/**
 * 顶栏工作区菜单（P7 T1，计划 D2）：▾ 下拉聚合 工作区选择 / 新建案卷 / 导出备份，
 * 替代原先散落下拉 + 按钮 + 链接三件套。条目构成规则在 state/workspaceMenu.ts
 * （纯函数可测），本组件只做开合调度与呈现；点外收起与 SearchBox 同款判定。
 */

import { useEffect, useRef, useState } from "react";
import { buildWorkspaceMenu } from "../state/workspaceMenu";
import { useChatStore } from "../state/store";

interface Props {
  onSwitch: (slug: string) => void;
  /** 打开新建案卷表单（表单本体仍由 App 渲染在顶栏下方的展开行）。 */
  onCreate: () => void;
}

export default function WorkspaceMenu({ onSwitch, onCreate }: Props) {
  const slug = useChatStore((s) => s.slug);
  const workspaces = useChatStore((s) => s.workspaces);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击菜单外 → 收起（mousedown 先于 blur，用 contains 判定，同 SearchBox）。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current !== null && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const items = buildWorkspaceMenu(slug, workspaces);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`flex max-w-44 items-center gap-1.5 border px-2 py-0.5 text-xs transition-colors ${
          open ? "border-gold/60 text-gold" : "border-line text-paper/75 hover:border-gold/50"
        }`}
      >
        <span className="truncate">{slug ?? "选择案卷…"}</span>
        <span aria-hidden="true" className="text-[9px] text-gold-soft">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute top-full right-0 z-50 mt-1 w-56 border border-line bg-ink py-1 shadow-[0_12px_40px_rgba(0,0,0,0.5)]"
        >
          {items.map((item, i) => {
            if (item.kind === "separator") {
              return <div key={i} className="my-1 border-t border-line/60" />;
            }
            if (item.kind === "create") {
              return (
                <button
                  key={i}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    onCreate();
                  }}
                  className="block w-full px-3 py-1.5 text-left text-xs text-gold transition-colors hover:bg-gold/10"
                >
                  ＋ 新建案卷
                </button>
              );
            }
            if (item.kind === "export") {
              return (
                <a
                  key={i}
                  role="menuitem"
                  href={`/api/${encodeURIComponent(item.slug)}/export`}
                  title="导出工作区全量备份（zip：账本/正文/设置/会话/事件）"
                  onClick={() => setOpen(false)}
                  className="block px-3 py-1.5 text-left text-xs text-paper/75 transition-colors hover:bg-gold/10 hover:text-gold"
                >
                  ⇩ 导出备份
                </a>
              );
            }
            return (
              <button
                key={i}
                type="button"
                role="menuitem"
                title={item.slug}
                onClick={() => {
                  setOpen(false);
                  if (!item.current) onSwitch(item.slug);
                }}
                className={`block w-full truncate px-3 py-1.5 text-left text-xs transition-colors hover:bg-gold/10 ${
                  item.current ? "text-gold" : "text-paper/75"
                }`}
              >
                <span aria-hidden="true" className="mr-1.5">
                  {item.current ? "◆" : "◇"}
                </span>
                {item.slug}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
