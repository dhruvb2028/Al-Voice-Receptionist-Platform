"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import type { MessageListItem } from "@/lib/api";
import {
  saveMessageNoteAction,
  setMessageReviewedAction,
} from "@/app/dashboard/messages/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";

function urgencyVariant(urgency: string) {
  switch (urgency) {
    case "emergency":
      return "danger" as const;
    case "urgent":
      return "warning" as const;
    default:
      return "neutral" as const;
  }
}

export function MessageCard({ message }: { message: MessageListItem }) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState(message.internal_note ?? "");
  const [isPending, startTransition] = useTransition();
  const { toast } = useToast();
  const reviewed = message.reviewed_at !== null;

  function toggleReviewed() {
    startTransition(async () => {
      const result = await setMessageReviewedAction(message.id, !reviewed);
      toast({
        title: result.ok ? "Updated" : "Couldn't update",
        description: result.message,
        tone: result.ok ? "success" : "error",
      });
    });
  }

  function saveNote() {
    startTransition(async () => {
      const result = await saveMessageNoteAction(message.id, note);
      if (result.ok) setNoteOpen(false);
      toast({
        title: result.ok ? "Note saved" : "Couldn't save note",
        description: result.message,
        tone: result.ok ? "success" : "error",
      });
    });
  }

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">
            {message.customer_name ?? "Caller"}
            {message.phone_last_four && (
              <span className="ml-2 text-sm tabular-nums text-muted-foreground">
                ···{message.phone_last_four}
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground">
            {new Date(message.created_at).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={urgencyVariant(message.urgency)}>{message.urgency}</Badge>
          <Badge variant={reviewed ? "success" : "warning"}>
            {reviewed ? "reviewed" : "needs review"}
          </Badge>
          <Badge variant={message.delivery_status === "sent" ? "info" : "neutral"}>
            {message.delivery_status}
          </Badge>
        </div>
      </div>

      <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
        {message.body ?? (
          <span className="text-muted-foreground">
            This message can&apos;t be displayed right now.
          </span>
        )}
      </p>

      {message.internal_note && !noteOpen && (
        <div className="mt-3 rounded-md border border-dashed border-border bg-muted/40 p-2.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Internal note · staff only
          </p>
          <p className="mt-1 text-sm">{message.internal_note}</p>
        </div>
      )}

      {noteOpen && (
        <div className="mt-3">
          <label
            htmlFor={`note-${message.id}`}
            className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
          >
            Internal note
          </label>
          <textarea
            id={`note-${message.id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={3}
            maxLength={2000}
            className="mt-1 w-full rounded-md border border-border bg-card p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
            placeholder="Visible only to your team — never spoken by the receptionist."
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={saveNote} disabled={isPending}>
              Save note
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setNote(message.internal_note ?? "");
                setNoteOpen(false);
              }}
              disabled={isPending}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={toggleReviewed} disabled={isPending}>
          {reviewed ? "Mark not reviewed" : "Mark reviewed"}
        </Button>
        {!noteOpen && (
          <Button size="sm" variant="secondary" onClick={() => setNoteOpen(true)}>
            {message.internal_note ? "Edit note" : "Add note"}
          </Button>
        )}
        {message.call_id && (
          <Link
            href={`/dashboard/calls/${message.call_id}`}
            className="text-sm text-primary hover:underline"
          >
            View call
          </Link>
        )}
      </div>
    </Card>
  );
}
