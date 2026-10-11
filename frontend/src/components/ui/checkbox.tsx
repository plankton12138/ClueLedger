/**
 * Checkbox（shadcn 模式，源码在仓可改）——Radix Checkbox 受控勾选。
 * 替代原生 accent-gold 勾选框：形态对齐（方框金勾），aria/键盘由 Radix 白拿。
 */
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const Checkbox = forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      "flex h-3.5 w-3.5 shrink-0 items-center justify-center border border-line bg-transparent",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
      "disabled:cursor-not-allowed disabled:opacity-40",
      "data-[state=checked]:border-gold data-[state=checked]:bg-gold data-[state=checked]:text-ink",
      className,
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator>
      <Check className="h-2.5 w-2.5" aria-hidden="true" />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = "Checkbox";

export { Checkbox };
