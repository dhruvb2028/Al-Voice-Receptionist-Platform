# Booking safety

Two failures matter more than any other: **double-booking** sends two
vans to one job, and **phantom confirmation** tells a caller they are
booked when nothing was written. Both destroy the client's trust in a
way a missed call does not.

## The rule

> A booking is announced only after the caller agreed **and** the write
> succeeded.

Never before. Not "I'll get that booked for you" spoken while the
calendar call is in flight — because if it fails, the caller has already
been told something untrue.

## The transaction

```mermaid
sequenceDiagram
    participant E as Engine
    participant DB as PostgreSQL
    participant Cal as Google Calendar

    E->>DB: SAVEPOINT
    E->>DB: INSERT booking (unique idempotency_key)
    alt duplicate key
        DB-->>E: IntegrityError
        E->>DB: ROLLBACK TO SAVEPOINT
        E->>DB: SELECT existing booking
        DB-->>E: the original
        Note over E: report the original; never a second row
    else inserted
        DB-->>E: pending booking
        E->>Cal: create event
        alt success
            Cal-->>E: event id
            E->>DB: status = confirmed, event id stored
            Note over E: only now may it say "booked"
        else failure or timeout
            E->>DB: status = reconciliation_required
            Note over E: take a message; never claim a booking
        end
    end
```

The savepoint matters: a duplicate-key `IntegrityError` poisons the
enclosing transaction in PostgreSQL. Rolling back to a savepoint lets the
conversation continue instead of losing the whole turn.

## Idempotency

`bookings.idempotency_key` is globally unique and derived from the call
and the requested slot, so:

- A retried Twilio webhook cannot create a second booking.
- A caller repeating "book Tuesday at ten" gets told it is already
  booked, rather than booked twice.
- Two workers racing produce one row; the loser reads the winner's.

Verified against a real database, not just mocks: a test races four
concurrent transactions on separate connections against one key and
asserts exactly one booking exists and every successful attempt points
at it.

## Availability

Availability is never inferred. `check_availability` reads the real
calendar; when the calendar is unreachable the tool returns a failure and
the guardrail pipeline blocks any reply that would state a time. The
receptionist takes a message instead.

This is why "calendar timeout" and "calendar revoked" are
safety-critical evaluation cases: the correct behaviour is to decline,
and the tempting behaviour is to guess.

## Reconciliation

A booking whose calendar write failed is marked
`reconciliation_required` rather than deleted. It is a real customer
commitment that a human must resolve — deleting it would lose the fact
that a caller was told anything at all.

Cancellation follows the same principle: the row moves to `cancelled`
and calendar cleanup is deferred to reconciliation, so a calendar outage
cannot block a cancellation the client asked for.

## What is tested

| Property | Where |
|---|---|
| Duplicate key returns the original | `test_tool_persistence.py` |
| Four concurrent transactions → one booking | `test_tool_persistence.py` |
| Unique constraint at the schema level | `test_schema_constraints.py` |
| Domain-level concurrency, one winner | `test_business_tools.py` |
| No confirmation before commit | evaluation, safety-critical |
| Calendar failure → message, no claim | evaluation, safety-critical |
| Repeat request → no second booking | evaluation, safety-critical |
| Post-call extraction never downgrades a confirmed booking | `test_post_call.py` |
