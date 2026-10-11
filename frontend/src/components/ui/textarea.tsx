/**
 * Textarea（shadcn 模式，源码在仓可改）——暗面多行输入（Composer 指令框）。
 * resize 行为由调用方决定（Composer 用 resize-none）。
 */
import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex w-full min-w-0 border border-line bg-ink text-paper",
        "px-3 py-2 text-sm",
        "placeholder:text-paper/30 focus:border-gold focus:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export { Textarea };
