/**
 * AlertDialog（shadcn 模式，源码在仓可改）——Radix 模态确认框，替代内联确认面板。
 *
 * 与普通 Dialog 的差别：带 overlay、Esc/点遮罩即取消、无右上角关闭钮——
 * 专用于「说明后果 + 显式确认」的不可逆/重操作入口（开新会话、删文章）。
 * 硬约束放组件层：焦点陷阱、Esc、遮罩、aria 全由 Radix 白拿。
 * 令牌映射：content=bg-surface/border-line/text-paper（项目直角），overlay=bg-overlay。
 */
import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { forwardRef } from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const AlertDialog = AlertDialogPrimitive.Root;
const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
const AlertDialogPortal = AlertDialogPrimitive.Portal;

const AlertDialogOverlay = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <AlertDialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-overlay",
      "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
      "data-[state=open]:animate-in data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
));
AlertDialogOverlay.displayName = "AlertDialogOverlay";

const AlertDialogContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Content>
>(({ className, ...props }, ref) => (
  <AlertDialogPortal>
    <AlertDialogOverlay />
    <AlertDialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
        "border border-line bg-surface p-4 text-paper shadow-surface",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        className,
      )}
      {...props}
    />
  </AlertDialogPortal>
));
AlertDialogContent.displayName = "AlertDialogContent";

function AlertDialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1.5", className)} {...props} />;
}

const AlertDialogTitle = forwardRef<
  HTMLHeadingElement,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <AlertDialogPrimitive.Title
    ref={ref}
    className={cn("text-sm font-semibold text-paper", className)}
    {...props}
  />
));
AlertDialogTitle.displayName = "AlertDialogTitle";

const AlertDialogDescription = forwardRef<
  HTMLParagraphElement,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <AlertDialogPrimitive.Description
    ref={ref}
    className={cn("text-xs leading-relaxed text-paper/70", className)}
    {...props}
  />
));
AlertDialogDescription.displayName = "AlertDialogDescription";

function AlertDialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("mt-3 flex flex-wrap items-center gap-2", className)} {...props} />
  );
}

/** 确认键（默认 outline 金描边；危险操作由调用方传 variant/danger 色）。 */
const AlertDialogAction = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, ...props }, ref) => (
    <AlertDialogPrimitive.Action asChild>
      <Button ref={ref} size="sm" variant="outline" className={cn("h-auto px-2.5 py-1 text-xs", className)} {...props} />
    </AlertDialogPrimitive.Action>
  ),
);
AlertDialogAction.displayName = "AlertDialogAction";

/** 取消键：Radix 关闭语义内建（Esc/遮罩/取消键同路）。 */
const AlertDialogCancel = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, ...props }, ref) => (
    <AlertDialogPrimitive.Cancel asChild>
      <Button ref={ref} size="sm" variant="outline" className={cn("h-auto px-2.5 py-1 text-xs", className)} {...props} />
    </AlertDialogPrimitive.Cancel>
  ),
);
AlertDialogCancel.displayName = "AlertDialogCancel";

export {
  AlertDialog,
  AlertDialogPortal,
  AlertDialogOverlay,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
};
