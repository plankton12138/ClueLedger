/**
 * Popover（shadcn 模式，源码在仓可改）——Radix Popover 浮层。
 *
 * 外点关闭/Esc/焦点归还由 Radix 白拿；非按钮触发（如搜索框聚焦展开）用
 * PopoverAnchor 定锚 + 受控 open，弹层位置锚在 anchor 上。
 * 令牌映射：content=bg-ink/border-line（保持原下拉暗底）+ 自定义投影。
 */
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const Popover = PopoverPrimitive.Root;
const PopoverTrigger = PopoverPrimitive.Trigger;
const PopoverAnchor = PopoverPrimitive.Anchor;
const PopoverPortal = PopoverPrimitive.Portal;

const PopoverContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = "start", sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      className={cn(
        "z-50 max-h-80 overflow-auto border border-line bg-ink text-paper",
        "shadow-[0_12px_40px_rgba(0,0,0,0.5)]",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0",
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = "PopoverContent";

export { Popover, PopoverTrigger, PopoverAnchor, PopoverPortal, PopoverContent };
