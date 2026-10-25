import { forwardRef, type ButtonHTMLAttributes } from "react";
interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "normal" | "compact";
  busy?: boolean;
  disabledReason?: string;
}
export const Button = forwardRef<HTMLButtonElement, Props>(function Button({ variant = "secondary", size = "normal", busy, disabledReason, className = "", children, disabled, ...props }, ref) {
  return <button {...props} ref={ref} type={props.type ?? "button"} className={`ui-button ${className}`} data-variant={variant} data-size={size}
    disabled={disabled || busy} aria-busy={busy || undefined} title={disabled && disabledReason ? disabledReason : props.title}>
    {busy && <span aria-hidden="true">…</span>}{children}
  </button>;
});
