/**
 * Sheet（shadcn 模式，源码在仓可改）——Radix Dialog 派生的侧滑抽屉，left/right 两向。
 *
 * 替代手写 fixed overlay：遮罩点击/Esc/焦点陷阱/aria 全部移交 Radix；
 * 把手（屏幕边缘竖排按钮）留在调用方，受控 open 驱动。
 * 令牌映射：overlay=bg-overlay，content=bg-ink/border-line（保持原抽屉定宽 w-80 max-w-[85vw]）。
 * <xl 显示侧用 className 传 xl:hidden 等（Portal 落在 body 上照样生效）。
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { forwardRef } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const Sheet = DialogPrimitive.Root;
const SheetTrigger = DialogPrimitive.Trigger;
const SheetPortal = DialogPrimitive.Portal;

const SheetOverlay = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
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
SheetOverlay.displayName = "SheetOverlay";

type SheetSide = "left" | "right";

const sideClasses: Record<SheetSide, string> = {
  left: "inset-y-0 left-0 h-full border-r border-line",
  right: "inset-y-0 right-0 h-full border-l border-line",
};

const sideAnimations: Record<SheetSide, string> = {
  left:
    "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-left " +
    "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-left",
  right:
    "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-right " +
    "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-right",
};

const SheetContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { side?: SheetSide }
>(({ className, side = "right", children, ...props }, ref) => (
  <SheetPortal>
    <SheetOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed z-50 flex w-80 max-w-[85vw] flex-col bg-ink",
        sideClasses[side],
        sideAnimations[side],
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="sr-only" aria-label="关闭">
        关闭
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </SheetPortal>
));
SheetContent.displayName = "SheetContent";

/** 抽屉头：标题行 + 关闭钮（保持原手写头的视觉与布局）。 */
function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-none items-center justify-between border-b border-line px-3 py-2",
        className,
      )}
      {...props}
    />
  );
}

const SheetTitle = forwardRef<
  HTMLHeadingElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-xs tracking-widest text-gold-soft", className)}
    {...props}
  />
));
SheetTitle.displayName = "SheetTitle";

const SheetDescription = forwardRef<
  HTMLParagraphElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-xs leading-relaxed text-paper/70", className)}
    {...props}
  />
));
SheetDescription.displayName = "SheetDescription";

/** × 关闭钮（Close 语义由 Radix 内建）。 */
const SheetClose = forwardRef<HTMLButtonElement, React.ComponentProps<typeof Button>>(
  ({ className, ...props }, ref) => (
    <DialogPrimitive.Close asChild>
      <Button
        ref={ref}
        variant="outline"
        className={cn(
          "h-auto px-1.5 py-0.5 text-xs text-paper/60 hover:bg-transparent hover:text-gold",
          className,
        )}
        {...props}
      />
    </DialogPrimitive.Close>
  ),
);
SheetClose.displayName = "SheetClose";

export {
  Sheet,
  SheetTrigger,
  SheetPortal,
  SheetOverlay,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetClose,
};
