"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  activateTenantAction,
  beginTestingAction,
  pauseTenantAction,
  type LifecycleResult,
} from "./actions";

interface Props {
  tenantId: string;
  status: string;
  ready: boolean;
}

/** Lifecycle actions with an explicit confirmation step — the API also
 *  refuses unconfirmed requests, this is not the only guard. */
export function LifecycleControls({ tenantId, status, ready }: Props) {
  const [confirming, setConfirming] = useState<null | "activate" | "pause" | "testing">(
    null,
  );
  const [result, setResult] = useState<LifecycleResult | null>(null);
  const [pending, startTransition] = useTransition();

  function run(action: () => Promise<LifecycleResult>) {
    startTransition(async () => {
      setResult(await action());
      setConfirming(null);
    });
  }

  const confirmLabel = {
    activate: "Activate this tenant? Real callers will reach the receptionist.",
    pause: "Pause this tenant? New calls will hear the unavailable message.",
    testing: "Move this tenant into testing?",
  } as const;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lifecycle</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {result && (
          <p
            role="status"
            className={
              result.ok ? "text-sm font-medium text-accent" : "text-sm text-destructive"
            }
          >
            {result.message}
          </p>
        )}

        {confirming ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
            <p className="text-sm text-amber-900">{confirmLabel[confirming]}</p>
            <div className="mt-3 flex gap-2">
              <Button
                size="sm"
                variant={confirming === "pause" ? "destructive" : "primary"}
                disabled={pending}
                onClick={() =>
                  run(
                    confirming === "activate"
                      ? () => activateTenantAction(tenantId)
                      : confirming === "pause"
                        ? () => pauseTenantAction(tenantId)
                        : () => beginTestingAction(tenantId),
                  )
                }
              >
                {pending ? "Working…" : "Confirm"}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={pending}
                onClick={() => setConfirming(null)}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {status === "onboarding" && (
              <Button size="sm" onClick={() => setConfirming("testing")}>
                Begin testing
              </Button>
            )}
            {(status === "testing" || status === "paused") && (
              <Button
                size="sm"
                disabled={status === "testing" && !ready}
                title={
                  status === "testing" && !ready
                    ? "Resolve all blockers first"
                    : undefined
                }
                onClick={() => setConfirming("activate")}
              >
                Activate
              </Button>
            )}
            {status === "active" && (
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setConfirming("pause")}
              >
                Pause
              </Button>
            )}
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Every lifecycle change is recorded in the audit log with your identity.
        </p>
      </CardContent>
    </Card>
  );
}
