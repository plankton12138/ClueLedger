/**
 * Input（shadcn 模式，源码在仓可改）——暗面文本输入基样式。
 * 项目紧凑风：默认 px-2 py-1 text-xs，尺寸/宽度由调用方 className 覆盖。
 * 羊皮纸面无边框特化输入不适用本组件（保留原生）。
 */
import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex w-full min-w-0 border border-line bg-transparent text-paper",
        "px-2 py-1 text-xs",
        "placeholder:text-paper/30 focus:border-gold/60 focus:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
