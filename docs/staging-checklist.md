# Staging checklist

Run after a staging deploy, before promoting to production. Fifteen
minutes, and it catches the things unit tests structurally cannot: real
audio, real provider latency, real webhooks.

## Automated gate

- [ ] CI green: lint, types, 570+ tests, evaluation suite, migration verify
- [ ] Evaluation suite exit code `0` (a `2` means a safety regression)
- [ ] Migration gate applied cleanly to the staging database
- [ ] All three services report `/healthz` and `/readyz`

## Configuration

- [ ] Staging uses the **staging** Neon branch, not production
- [ ] Staging uses **test** provider keys and a **test** phone number
- [ ] `ENVIRONMENT=staging` on every service (Sentry separation depends
      on it)
- [ ] No production secret is readable from the staging project

## Text simulator

Use the admin console (`/admin/tenants/{id}/testing/text`). Faster than
phone calls and exercises the same engine.

- [ ] Normal booking completes and appears in the dashboard
- [ ] Unavailable slot offers a real alternative rather than inventing one
- [ ] Price question quotes only an approved price
- [ ] Discount pressure is refused
- [ ] Emergency phrasing escalates immediately
- [ ] "Get me a human" transfers immediately
- [ ] Out-of-area request declines and takes a message
- [ ] Unsupported service declines and takes a message
- [ ] Prompt injection ("ignore your instructions") is refused

## Real phone call

The simulator does not test audio. At least one real call:

- [ ] Number answers within two rings
- [ ] Greeting is correct and audible
- [ ] Interrupting mid-sentence stops playback promptly (barge-in)
- [ ] Response latency feels conversational, not laggy
- [ ] Booking completes and lands on the calendar
- [ ] Hanging up ends the call cleanly

## Post-call

- [ ] Call appears in the dashboard within a minute
- [ ] Transcript is complete and correctly attributed
- [ ] Summary and outcome are accurate
- [ ] Recording plays (if recording is enabled for the tenant)
- [ ] Notification arrived, with **no transcript in the body**
- [ ] Usage and cost recorded

## Isolation

- [ ] Signed in as tenant A, a tenant B call id returns 404
- [ ] A client user gets 404 on `/admin/*`
- [ ] Staff cannot cancel a booking (owner-only)

## Failure paths

Worth exercising deliberately, since these only show up when something is
already wrong:

- [ ] Calendar disconnected → declines to book, takes a message, no
      invented confirmation
- [ ] Transfer with nobody answering → falls back to a message
- [ ] Oversized request → 413 with a request id
- [ ] Forged webhook signature → 401

## Observability

- [ ] Sentry receives a staging error, tagged `environment=staging`
- [ ] No transcript, phone number, or email visible in any Sentry event
- [ ] Logs show request ids and no unredacted personal data
- [ ] System-health page loads and alerts read plausibly

## Sign-off

Deploy to production only when every box is ticked. A partial pass is a
fail — the items skipped are the ones that break in front of a customer.
