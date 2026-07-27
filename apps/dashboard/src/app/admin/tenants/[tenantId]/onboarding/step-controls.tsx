"use client";

import { useState, useTransition } from "react";
import type { OnboardingStep } from "@/lib/api";
import { recordStepAction, waiveStepAction } from "./actions";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

/** Sign-off and waiver controls for one attested step. Derived steps
 *  render nothing — they are true or false based on the data, and the
 *  API refuses to let them be ticked by hand. */
export function StepControls({
  tenantId,
  step,
}: {
  tenantId: string;
  step: OnboardingStep;
}) {
  const [waiving, setWaiving] = useState(false);
  const [reason, setReason] = useState("");
  const [isPending, startTransition] = useTransition();
  const { toast } = useToast();

  if (!step.attested || step.key === "activation") return null;

  const done = step.status === "complete";

  function run(action: () => Promise<{ ok: boolean; message: string }>) {
    startTransition(async () => {
      const result = await action();
      if (result.ok) {
        setWaiving(false);
        setReason("");
      }
      toast({
        title: result.ok ? "Updated" : "Couldn't update",
        description: result.message,
        tone: result.ok ? "success" : "error",
      });
    });
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={done ? "secondary" : "primary"}
          disabled={isPending}
          onClick={() => run(() => recordStepAction(tenantId, step.key, !done))}
        >
          {done ? "Undo sign-off" : "Sign off"}
        </Button>
        {step.waivable && !done && !waiving && (
          <Button
            size="sm"
            variant="secondary"
            disabled={isPending}
            onClick={() => setWaiving(true)}
          >
            Waive
          </Button>
        )}
      </div>

      {waiving && (
        <div className="mt-2">
          <label
            htmlFor={`waive-${step.key}`}
            className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
          >
            Why is this being skipped?
          </label>
          <textarea
            id={`waive-${step.key}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            minLength={10}
            className="mt-1 w-full rounded-md border border-border bg-card p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
            placeholder="Recorded against your name on the activation report."
          />
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              disabled={isPending || reason.trim().length < 10}
              onClick={() => run(() => waiveStepAction(tenantId, step.key, reason))}
            >
              Record waiver
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={isPending}
              onClick={() => {
                setWaiving(false);
                setReason("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
