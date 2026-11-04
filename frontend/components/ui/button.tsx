import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost";
};

export function Button({ className, variant = "default", ...props }: ButtonProps) {
  const variants = {
    default: "bg-violet-500 text-white hover:bg-violet-400",
    outline: "border border-white/15 bg-transparent text-slate-100 hover:bg-white/5",
    ghost: "bg-transparent text-slate-300 hover:bg-white/5",
  };
  return <button className={cn("inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50", variants[variant], className)} {...props} />;
}
