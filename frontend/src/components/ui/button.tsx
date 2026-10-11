/**
 * Button（shadcn 模式，源码在仓可改）——Radix Slot + CVA 变体。
 *
 * 变体选择：
 * - default：暗金实心主操作（提交/保存/确认）
 * - soft：金描边淡金底（对话区选项/空态建议，等价原 ActionButton）
 * - outline：线框次操作（面板内普通按钮）
 * - ghost：无底无线（图标/顶栏弱操作）
 * - danger：红底（不可逆操作确认键）
 * - parchment：羊皮纸浅底面专用（深墨字 + parchment-line 描边，禁止用于暗面）
 * 视觉基线保持项目直角风格（历史 69 处裸按钮均无圆角）。
 */
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-40",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-gold-soft",
        soft: "border border-gold/50 bg-gold/10 text-gold hover:bg-gold/20",
        outline: "border border-line text-paper hover:bg-accent",
        ghost: "text-paper/80 hover:bg-accent hover:text-paper",
        danger: "bg-danger text-danger-foreground hover:bg-overdue/85",
        parchment:
          "border border-parchment-line text-parchment-ink hover:bg-parchment-soft",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-8 px-3 text-xs",
        xs: "px-2.5 py-1 text-xs",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = "button", ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={type}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
