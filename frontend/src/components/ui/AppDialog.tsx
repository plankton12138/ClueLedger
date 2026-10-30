import { useRef, type ReactNode, type ReactElement } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "./Button";
/** 业务状态由调用者保留；此处只管理焦点、Esc、遮罩与关闭后的入口。 */
export function AppDialog({ open, onOpenChange, title, children, trigger, returnFocus, initialFocus, className = "" }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode; trigger?: ReactElement; returnFocus?: () => HTMLElement | null; initialFocus?: () => HTMLElement | null; className?: string;
}) {
  const origin = useRef<HTMLElement | null>(null);
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    {trigger && <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>}
    <Dialog.Portal><Dialog.Overlay className="ui-dialog-overlay" />
      <Dialog.Content className={`ui-dialog ${className}`} aria-describedby={undefined}
        onOpenAutoFocus={event => {
          origin.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          const target = initialFocus?.();
          if (target) { event.preventDefault(); target.focus(); }
        }}
        onCloseAutoFocus={event => { const target = origin.current?.isConnected && origin.current !== document.body ? origin.current : returnFocus?.(); if (target) { event.preventDefault(); target.focus(); } }}>
        <header className="ui-dialog-header"><Dialog.Title>{title}</Dialog.Title><Dialog.Close asChild><Button size="compact" variant="quiet">关闭</Button></Dialog.Close></header>
        <div className="ui-dialog-body">{children}</div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
