# Privacy

What personal data this platform holds, why, for how long, and how it
leaves. Written for the operator and for a client's own privacy review.

## Roles

Each client business is the **data controller** for its callers' data.
The platform operator is a **processor** acting on that client's
instructions. Sub-processors are listed below; a client adding the
platform to their own privacy notice needs that list.

## What is collected

| Data | Source | Why | Storage |
|---|---|---|---|
| Caller phone number | Telephony | Identify and call back | Encrypted + keyed hash + last four |
| Caller name | Conversation | Booking and message records | Encrypted where free text |
| Service address | Conversation | Dispatch the visit | Encrypted |
| Message content | Conversation | Deliver the message | Encrypted |
| Transcript | Speech-to-text | Summary, quality, dispute resolution | Plain text, tenant-scoped |
| Call recording | Telephony | Quality and dispute resolution | R2 object, tenant-prefixed key |
| Call metadata | Platform | Billing, analytics, reliability | Plain, tenant-scoped |
| Staff identity | Clerk | Dashboard access control | External user id only |

The platform does **not** collect payment card data, government
identifiers, or health information, and the receptionist is not
instructed to ask for them.

## Recording consent

Recording is **off by default** and enabled per tenant. When enabled, the
consent notice is part of the tenant's approved configuration and plays
at call start. A tenant operating where consent rules differ configures
its own notice — the platform does not assume one jurisdiction's rules
apply everywhere.

## Retention

| Data | Default | Maximum | Mechanism |
|---|---|---|---|
| Recordings | 30 days | 90 days | Scheduled sweep, audited per deletion |
| Transcripts and call metadata | Life of the account | — | Deleted on contractual purge |
| Bookings and messages | Life of the account | — | Business records the client relies on |
| Audit logs | Life of the account | — | Integrity record; not purged with content |
| Notification deliveries | Life of the account | — | Recipients stored masked only |

A **legal hold** exempts a recording from retention deletion. Holds are
set deliberately and are visible on the call record.

Deleting a tenant is a soft archive (`archived_at`), not a cascade —
call, usage, and audit history are protected by `RESTRICT` foreign keys
so nothing is destroyed by accident. Contractual purge is an explicit,
audited procedure.

## Data minimisation in practice

- Only a recording's **object key** is stored in PostgreSQL. Never
  audio, never a URL.
- The dashboard shows `···4821` rather than a full number wherever the
  full value is not needed.
- Notification records store a **masked** recipient, so the delivery log
  is not itself a contact list.
- The email suppression list stores keyed **hashes**, so an unsubscribe
  list cannot be reused as a mailing list.
- Notifications carry a summary and a dashboard link — never a
  transcript, recording, message body, or caller contact detail. This is
  enforced in code (`assert_safe_variables`), not by convention.
- Internal notes live in a column the conversation engine never selects,
  so the receptionist cannot read a staff note aloud.

## Sub-processors

| Processor | Purpose | Data seen |
|---|---|---|
| Twilio | Telephony, SMS | Phone numbers, audio, message text |
| Deepgram | Speech-to-text | Call audio |
| Cartesia | Text-to-speech | Reply text |
| Groq | Language model | Conversation text |
| Google Calendar | Booking write | Booking details, on tenant connection |
| Resend | Email delivery | Recipient address, template content |
| Cloudflare R2 | Recording storage | Call audio |
| Upstash QStash | Job delivery | Call identifiers |
| Clerk | Dashboard identity | Staff identity |
| Neon / Postgres | Primary datastore | All of the above |

Adding a sub-processor is a change clients must be told about.

## Access

- Client staff see only their own tenant, enforced twice (application
  scope and database RLS).
- Platform admins can access tenant data for support. Recording access
  is audited on every signed-URL mint, with the actor recorded.
- Providers receive only what their function requires.

## Individual rights

A caller's request reaches the client business, which asks the operator
to act. Supported:

- **Access** — call history, transcripts, bookings, and messages export
  from the dashboard as CSV.
- **Erasure** — a targeted purge of a caller's records; the audit trail
  retains the fact of the deletion, not the content.
- **Rectification** — booking and message records are editable by the
  client.
- **Objection to recording** — the tenant disables recording, or a
  specific recording is deleted ahead of its retention window.

## Cross-border transfers

Providers above operate in the United States and the European Union. A
client with data-residency obligations must confirm the operator's
current region configuration before onboarding; the platform does not
guarantee residency by default.

## Breach handling

See [incident-response.md](incident-response.md). Notification
obligations run controller-first: the operator informs the affected
client, and the client notifies its callers and regulator as its own
obligations require.
