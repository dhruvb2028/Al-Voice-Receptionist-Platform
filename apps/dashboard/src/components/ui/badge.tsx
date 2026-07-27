import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        neutral: "bg-muted text-muted-foreground",
        info: "bg-blue-100 text-blue-800",
        success: "bg-emerald-100 text-emerald-800",
        warning: "bg-amber-100 text-amber-800",
        danger: "bg-red-100 text-red-800",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

/** Tenant lifecycle → badge tone. */
export function statusVariant(
  status: string,
): VariantProps<typeof badgeVariants>["variant"] {
  switch (status) {
    case "active":
      return "success";
    case "testing":
      return "info";
    case "onboarding":
      return "neutral";
    case "paused":
      return "warning";
    case "suspended":
    case "churned":
      return "danger";
    default:
      return "neutral";
  }
}

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
