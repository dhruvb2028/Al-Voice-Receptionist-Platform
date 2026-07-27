import Link from "next/link";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface StateProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

/** Empty state — nothing exists yet; explains what will appear here. */
export function EmptyState({ title, description, action }: StateProps) {
  return (
    <Card className="flex flex-col items-center justify-center p-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </Card>
  );
}

/** Error state — the request failed; offers retry. */
export function ErrorState({
  title = "Something went wrong",
  description,
  retryHref,
}: {
  title?: string;
  description?: string;
  retryHref?: string;
}) {
  return (
    <Card className="flex flex-col items-center justify-center p-10 text-center" role="alert">
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {retryHref && (
        <Link href={retryHref} className="mt-4">
          <Button variant="secondary" size="sm">
            Try again
          </Button>
        </Link>
      )}
    </Card>
  );
}

/** Permission state — authenticated but not allowed. */
export function PermissionState({
  title = "You don't have access to this page",
  description = "Ask the account owner to adjust your permissions if you need it.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <Card className="flex flex-col items-center justify-center p-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
    </Card>
  );
}
