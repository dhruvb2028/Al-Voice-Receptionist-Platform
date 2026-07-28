# Delivery pack

Client-facing documents. Everything else in this repository is written
for whoever maintains the platform; **these are written for the business
buying it** and can be sent as-is.

## Before you send anything

The platform has not yet answered a real phone call. Read
[../PRODUCTION_READINESS.md](../PRODUCTION_READINESS.md) first.

That does not stop you delivering — it changes *what* you are
delivering. You are handing over a platform and starting a supervised
setup, not switching on a finished phone line today. Every document here
is written to match that reality, so a client who reads them will not be
surprised later.

**Do not tell a client their number is ready until it has answered a
real test call.** Everything in this pack is designed so you never have
to.

## What to send, and when

| # | Document | Send | Purpose |
|---|---|---|---|
| 01 | [What you're getting](01-what-you-are-getting.md) | At signing | Sets expectations before any work starts |
| 02 | [Setup form](02-setup-form.md) | Immediately after | Everything needed to configure their receptionist |
| 03 | [What happens next](03-what-happens-next.md) | With the setup form | The timeline and what you need from them |
| 04 | [Approvals](04-approvals.md) | Once configured | Sign-offs before anything goes live |
| 05 | [Your dashboard](05-your-dashboard.md) | At go-live | How to use it day to day |
| 06 | [Service terms](06-service-terms.md) | At signing | What is and isn't promised |

## Placeholders

Search for `{{` before sending anything. Each needs a real value:

`{{BRAND_NAME}}` · `{{YOUR_NAME}}` · `{{CONTACT_EMAIL}}` ·
`{{CONTACT_PHONE}}` · `{{DASHBOARD_URL}}` · `{{PRICE_MONTHLY}}` ·
`{{PRICE_INCLUDED_CALLS}}` · `{{PRICE_OVERAGE}}` · `{{SUPPORT_HOURS}}`

Nothing here invents a statistic, a testimonial, or a result. If you add
figures later, use measured ones from the client's own dashboard.

## Your side of the handover

The client-facing documents above pair with internal ones:

- [../docs/client-launch.md](../docs/client-launch.md) — your checklists,
  the test-call script, and the stop conditions
- [../docs/client-onboarding.md](../docs/client-onboarding.md) — how the
  onboarding workflow computes readiness
- [../docs/support.md](../docs/support.md) — triage once they are live

Work the onboarding checklist in the admin console at
`/admin/tenants/{id}/onboarding`. It generates the handover checklist,
test-call report, and activation report that evidence the launch.
