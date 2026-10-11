/**
 * Tabs（shadcn 模式，源码在仓可改）——Radix Tabs 受控切换。
 *
 * 形态对齐原手写 Tab 按钮组（直角、active=金描边淡金底）：
 * data-[state=active/inactive] 驱动双色，方向键/aria-selected/focus 由 Radix 白拿。
 * 内容区可用 TabsContent，也可继续条件渲染（受控 value 即可）。
 */
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const Tabs = TabsPrimitive.Root;

const TabsList = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn("flex items-center gap-1 outline-none", className)}
    {...props}
  />
));
TabsList.displayName = "TabsList";

const TabsTrigger = forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex h-auto items-center justify-center gap-1.5 whitespace-nowrap border border-transparent px-2 py-0.5 text-xs transition-colors",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
      "disabled:pointer-events-none disabled:opacity-40",
      "data-[state=active]:border-gold data-[state=active]:bg-gold/15 data-[state=active]:text-gold",
      "data-[state=inactive]:text-gold-soft data-[state=inactive]:hover:border-gold/50 data-[state=inactive]:hover:bg-transparent",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = "TabsTrigger";

const TabsContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("outline-none", className)}
    {...props}
  />
));
TabsContent.displayName = "TabsContent";

export { Tabs, TabsList, TabsTrigger, TabsContent };
