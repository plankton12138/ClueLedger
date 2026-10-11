/**
 * Badge（shadcn 模式）——全圆角小徽章，等价原 ManuscriptPanel Chip 与
 * 顶栏阶段/对账卡状态徽章。语义色直接对应账本伏笔生命周期令牌。
 * 两套承载面：暗面（默认诸变体）与羊皮纸面（parchment）。
 */
import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[10.5px] leading-tight",
  {
    variants: {
      variant: {
        neutral: "border-line bg-white/5 text-paper/70",
        gold: "border-gold/50 bg-gold/10 text-gold",
        info: "border-planned/50 bg-planned/10 text-planned",
        success: "border-planted/50 bg-planted/10 text-planted",
        danger: "border-overdue/50 bg-overdue/10 text-overdue",
        violet: "border-violet/50 bg-violet/10 text-violet",
        parchment: "border-parchment-line text-parchment-mute",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
