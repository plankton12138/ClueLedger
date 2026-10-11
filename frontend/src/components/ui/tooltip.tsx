/**
 * Tooltip（shadcn 模式，源码在仓可改）——Radix Tooltip 悬浮提示。
 *
 * 替代图标按钮的原生 title：delayDuration 延迟弹出、样式统一（暗底令牌）；
 * 键盘聚焦同样触发（Radix 白拿）。Provider 置于调用处局部即可。
 */
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 border border-line bg-ink px-2 py-1 text-xs text-paper",
        "shadow-[0_8px_24px_rgba(0,0,0,0.5)]",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0",
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = "TooltipContent";

export { TooltipProvider, Tooltip, TooltipTrigger, TooltipContent };
