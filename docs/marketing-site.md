# Marketing website — specification and build plan

The site sells a phone system to people who run trades businesses. It has
one job: make a busy owner believe this will stop them missing calls, and
get them to request a demo.

Built in Framer. This document is the complete specification — structure,
copy, and build steps.

## Placeholders

Every value below in `{{DOUBLE_BRACES}}` must be filled before launch.
**Do not invent any of them.** Shipping a placeholder is embarrassing;
shipping a fabricated statistic is a lie a customer can catch.

| Placeholder | Meaning | Blocked until |
|---|---|---|
| `{{BRAND_NAME}}` | Product name | Naming decided |
| `{{DEMO_NUMBER}}` | Live demo receptionist number | Number provisioned and tested |
| `{{CONTACT_EMAIL}}` | Sales contact | Mailbox exists |
| `{{PRICE_MONTHLY}}` | Monthly price | Pricing decided |
| `{{PRICE_INCLUDED_CALLS}}` | Calls included | Pricing decided |
| `{{PRICE_OVERAGE}}` | Per-call overage | Pricing decided |
| `{{CASE_STUDY_*}}` | Real customer story | A real customer consents |
| `{{METRIC_*}}` | Measured platform numbers | Enough real calls to measure |
| `{{COMPANY_LEGAL_NAME}}`, `{{COMPANY_ADDRESS}}` | Legal entity | Company registered |

### The honesty rule

Until there is a real customer and real measured data:

- **No testimonials.** Not "illustrative", not "representative". None.
- **No customer logos.** Not even greyed-out "as used by" strips.
- **No metrics.** No "95% of calls answered", no "3x more bookings", no
  "$40,000 recovered". Every such number on a launch site is invented,
  and buyers in this market have learned to discount them.
- **No revenue claims.** "Stop missing calls" is a capability. "Earn
  £30k more" is a forecast about someone else's business.

Where a testimonial section would go, ship a **product demonstration**
instead: a real transcript from the demo number, with the caller's
details redacted. It is more persuasive than a quote anyone could write.

---

## Pages

| Page | Path | Purpose |
|---|---|---|
| Home | `/` | Problem → product → proof → demo |
| Product | `/product` | What it does, in detail |
| Industries | `/industries` | Plumbing, electrical, HVAC framing |
| How it works | `/how-it-works` | Setup and the call path |
| Pricing | `/pricing` | Plan and what is included |
| Security | `/security` | For the cautious buyer |
| Demo | `/demo` | Call the number, or book a walkthrough |
| Contact | `/contact` | Form and direct contact |
| Privacy | `/privacy` | Legal |
| Terms | `/terms` | Legal |
| Login | → dashboard | Link out, not a page |

---

## Homepage

### 1. Hero

> # Never miss another job because nobody answered.
>
> {{BRAND_NAME}} answers your phone when you can't — on a roof, under a
> sink, or after hours. It books the job into your calendar, takes a
> message, and puts real emergencies straight through to you.
>
> **[Request a demo]** **[Call the demo receptionist — {{DEMO_NUMBER}}]**

The secondary CTA is the strongest asset on the site: a prospect can
verify the entire product in ninety seconds, without talking to sales.
Make the number tappable on mobile.

### 2. The problem

> ## The call you miss is the job you lose.
>
> When a tap is leaking, people call down the list until someone picks
> up. If you're on a job, that's the next name — not you.
>
> Voicemail doesn't fix it. Most callers with an urgent problem hang up
> rather than leave a message, and the ones who do leave one have often
> already booked elsewhere by the time you call back.

No statistic here. The reader already knows this is true from their own
week; a fabricated number would only invite doubt.

### 3. What it does

Four columns, plain language:

- **Answers every call** — first ring, day or night, including when
  you're already on the phone.
- **Books the work** — checks your real availability and writes the
  appointment into your calendar.
- **Takes proper messages** — name, number, address, and what's wrong,
  in your dashboard within a minute.
- **Knows what's urgent** — a gas smell or a burst pipe gets transferred
  to you immediately, not written down for later.

### 4. How a call goes

A visual sequence, not a robot illustration:

```
Phone rings  →  Answers in your business's name
             →  Understands what the caller needs
             →  Checks the calendar  →  Books it
                                     →  or takes a message
                                     →  or transfers an emergency to you
             →  You get a notification and a summary
```

### 5. Hear it yourself

The section that replaces testimonials.

> ## Don't take our word for it. Call it.
>
> {{DEMO_NUMBER}} is a live receptionist configured for a demo plumbing
> business. Try to catch it out — ask for a price it shouldn't know, ask
> for a Sunday slot, tell it your boiler is leaking gas.
>
> **[Call the demo receptionist]**

Below, a real redacted transcript from that number with the tool actions
shown inline. This proves the product works better than any claim.

### 6. Your dashboard

Screenshot of the real calls list and a call detail page. Caption:

> Every call, with a transcript, a summary, and what happened. Search it,
> filter it, export it.

Use genuine screenshots with placeholder tenant data — never a mockup of
a dashboard that does not exist.

### 7. Built to be trusted

> Your customers' details are encrypted. Recordings are off unless you
> turn them on, and deleted on a schedule you choose. The receptionist
> can only quote prices you've approved — it cannot invent one, and it
> cannot promise a time your calendar doesn't have.

Link to `/security`.

### 8. Close

> ## See it answer your calls.
>
> A 20-minute walkthrough. We'll set it up with your services, your
> hours, and your prices, and you can call it yourself.
>
> **[Request a demo]**

---

## Other pages

**Product** — expands each capability with the honest boundary alongside
it. Stating what it does *not* do ("it won't quote a price you haven't
approved") builds more trust than another benefit bullet.

**Industries** — plumbing, electrical, HVAC. Same product, the caller's
own vocabulary. No invented sector statistics.

**How it works** — setup (you send services, hours, and prices; we
configure it; you test it; we go live) and the call path. Be honest that
setup involves a human: it is a differentiator against self-serve tools
that never get configured properly.

**Pricing** — one plan, shown plainly.

> {{PRICE_MONTHLY}} per month · {{PRICE_INCLUDED_CALLS}} calls included ·
> {{PRICE_OVERAGE}} per additional call

With: what's included, what isn't, no setup fee if true, and cancellation
terms. If pricing isn't decided, ship "Contact us for pricing" rather
than a placeholder number — an invented price is worse than no price.

**Security** — a plain-English version of [security.md](security.md) for
a non-technical buyer: encryption, tenant separation, recording control,
retention, and who can see what. Link the privacy policy.

**Demo** — the number, what to try, and a booking form.

**Contact** — name, business, phone, email, message. Set expectations for
response time and honour them.

**Privacy / Terms** — from `{{COMPANY_LEGAL_NAME}}`'s counsel, consistent
with [privacy.md](privacy.md). Do not ship a generic template: this
product records phone calls, and a mismatched policy is a real liability.

---

## Design direction

Premium B2B, trades-aware. The buyer is a competent tradesperson who
distrusts slick software marketing.

**Palette.** One deep, confident primary (navy or forest), warm neutral
backgrounds, a single accent for CTAs. No purple-to-pink gradients — that
is the visual signature of AI hype, and this product's pitch is
reliability.

**Type.** One strong grotesque (Inter, Söhne, or similar) at generous
size. Headlines short and declarative.

**Imagery.** Real photography of trades work and real product
screenshots. **Explicitly forbidden:** robot faces, humanoid assistants,
glowing brains, neural-network meshes, waveform-as-decoration, and
circuit-board patterns. The product is a receptionist, not a robot; the
imagery should look like a business tool, not science fiction.

**Motion.** Restrained. Short fades and small offsets on scroll. Respect
`prefers-reduced-motion`. No parallax, no counters spinning up to a
number, no autoplaying video.

**Mobile first.** Owners will read this on a phone, in a van, in poor
light. Large tap targets; the demo number as a tap-to-call link.

---

## Framer build plan

1. **Project setup** — new Framer project, custom domain, `{{BRAND_NAME}}`
   in site settings.
2. **Design tokens** — colour, type scale, and spacing as Framer
   variables first, so pages stay consistent and a rebrand is one edit.
3. **Components** — Nav, Footer, CTA band, Feature card, Step, Pricing
   card, Contact form, Transcript block. Build these before pages.
4. **Homepage** — sections in the order above.
5. **Remaining pages** — reuse components; only Pricing and Security need
   bespoke layout.
6. **Forms** — Framer Forms to `{{CONTACT_EMAIL}}`, plus a webhook into
   the admin onboarding flow. Confirm both fire before launch.
7. **SEO** — per-page title and description, Open Graph image, sitemap,
   `robots.txt`. Local schema markup if targeting a service area.
8. **Analytics** — privacy-respecting (Plausible or Fathom). No
   third-party ad pixels on a site whose selling point is data care.
9. **Accessibility** — check contrast, focus states, alt text, and
   keyboard navigation. The security page especially must be readable.
10. **Performance** — compress images, lazy-load below the fold, target
    Lighthouse 90+ on mobile.

### Pre-launch gate

- [ ] Every `{{PLACEHOLDER}}` replaced — search the published site
- [ ] Demo number called from an external phone and it works
- [ ] Contact form delivers, and the reply address is monitored
- [ ] No testimonial, logo, metric, or revenue claim that isn't real
- [ ] Privacy and Terms reviewed by counsel
- [ ] Login link points at the live dashboard
- [ ] Mobile pass on a real phone, not a simulator
- [ ] No robot imagery anywhere

## Adding proof later

Once there is a consenting customer and enough real calls:

1. Replace `{{METRIC_*}}` with **measured** figures from the platform's
   own overview, stated with their window ("across 1,200 calls in
   November").
2. Add the case study with the customer's written consent, naming what
   was actually measured rather than what it might be worth.
3. Keep the demo number prominent regardless. A prospect verifying the
   product themselves outperforms any claim about someone else's results.
