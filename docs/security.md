# Security

How this platform is defended, where each control lives, and what a
reviewer should check. Controls are described as implemented, not as
aspirations — every claim below points at code or a test.

## Trust model

The platform is multi-tenant. The strongest assumption we make is that
**a tenant may be hostile**, and the second strongest is that **a caller
may be hostile**. Neither is trusted to supply a tenant identifier, and
neither can reach another tenant's data even with a valid session.

Untrusted inputs, in order of exposure:

| Input | Boundary | Control |
|---|---|---|
| Phone callers (speech) | `services/voice` | Guardrail pipeline, closed tool set |
| Twilio webhooks | `services/api/routers/webhooks.py` | HMAC-SHA1 signature verification |
| QStash job deliveries | `services/worker/qstash.py` | JWT signature, URL + body-hash claims |
| Media WebSocket | `services/voice/media_ws.py` | Single-use signed call token |
| Dashboard users | `services/api` | Clerk JWT, tenant-scoped repositories |
| Provider responses | `packages/providers` | Typed adapters, strict parsing |

## Authentication

Dashboard and admin requests carry a Clerk-issued JWT, verified against
Clerk's JWKS with issuer and audience checks (`api/auth/verify.py`). A
token signed by the wrong key, for the wrong audience, or past expiry is
rejected before any handler runs.

Machine-to-machine callers never use a user token:

- **Twilio** signs webhooks; we recompute
  `base64(HMAC-SHA1(auth_token, url + sorted_params))` and compare in
  constant time.
- **QStash** signs job deliveries as a JWT; we verify the signature, the
  `exp` claim, that `sub` matches the destination URL, and that the body
  hash matches the bytes we received. Current and next signing keys are
  both accepted so key rotation never drops a job.
- **The voice media socket** requires a single-use signed call token
  (`ai_shared/call_tokens.py`). The token's `jti` is consumed on first
  use, so a captured URL cannot be replayed.

## Authorization and tenant isolation

Two independent layers, because one is not enough:

1. **Application scope.** Client routes derive `tenant_id` exclusively
   from the verified principal. No client endpoint reads a tenant id
   from the path, query, or body. Queries go through tenant-scoped
   repositories that inject the filter.
2. **Database RLS.** Every tenant-owned table has row-level security
   keyed on the `app.tenant_id` GUC. A query that forgets its filter
   returns nothing rather than another tenant's rows.

A cross-tenant identifier returns **404, not 403** — a 403 confirms the
row exists, which is itself a leak.

Admin endpoints depend on `require_platform_admin`, which returns 404
for non-admin principals, so the admin surface is invisible rather than
merely forbidden.

Suspended and churned tenants are blocked at the dependency layer
(`_BLOCKED_TENANT_STATUSES`), and a member whose status is not `ACTIVE`
is refused. Revoking access is therefore a status change, not a deploy.

## Transport controls

`ai_shared/security.py`, attached to every service by
`configure_service_app`:

- **Security headers** on every response including error paths:
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cross-Origin-Opener-Policy`, `Permissions-Policy`, HSTS, and a
  default-deny CSP.
- **Request size limit** (1 MiB) enforced from `Content-Length` before
  the body is buffered. Routes reading raw bytes use
  `enforce_body_limit` for chunked requests that carry no length.
- **Rate limiting**, a fixed-window counter charged to the authenticated
  principal where present and the client address otherwise, so one noisy
  caller cannot exhaust another's budget. Rejections carry `Retry-After`.

Middleware order is deliberate: the security layer runs *inside* the
request-ID layer, so a rejected request still returns a request id and
remains traceable.

## Secrets

Configuration values may be literal or a `sm://` Secret Manager
reference; `ai_shared/secrets.py` resolves either and call sites cannot
tell the difference. Resolved values are cached per process, never
logged, and `SecretResolver.__repr__` is overridden so a secret cannot
surface in a traceback.

No secret is committed. CI and deploy workflows read from GitHub
Actions secrets and Secret Manager.

## Data protection

- **Encryption at rest, application layer.** Caller numbers, addresses,
  message bodies, internal notes, and OAuth tokens are AES-256-GCM
  encrypted via `ai_shared/crypto.py`. Ciphertext is versioned (`v1:`)
  so keys and algorithms can rotate alongside existing rows.
- **Keyed lookup hashes.** Equality search ("calls from this number")
  uses an HMAC-SHA256 column, so the database can match without holding
  the plaintext and hashes cannot be brute-forced without the key.
- **Display fragments.** `*_last_four` columns exist so the dashboard
  can identify a caller without decrypting anything.
- **Recordings** are stored in R2 under a tenant-prefixed key. Only the
  object key is in PostgreSQL — never audio, never a URL. Access is a
  short-lived (15 minute) signed URL minted after a tenant-scoped
  authorization check, and every mint is audited. No permanent public
  URL exists anywhere.
- **Retention.** A scheduled sweep deletes recordings past their
  tenant's window (30 days default, 90 day cap), exempting legal holds,
  and audits every deletion.

## Log hygiene

Redaction is a structlog processor (`ai_telemetry/logging.py`) — one
choke point rather than per-call-site discipline. It redacts known
sensitive keys, any key containing `password`, `secret`, `token`,
`api_key`, `auth`, or `signature`, and scrubs phone numbers and email
addresses from free text. Nested dicts and lists are walked to a bounded
depth. Masked forms (`···4821`) are deliberately preserved.

## Injection resistance

- **SQL.** All queries are SQLAlchemy Core/ORM with bound parameters.
  The only f-string SQL is in test teardown over a fixed table
  allowlist.
- **Prompt injection.** The receptionist has no free-text outbound
  capability. It can call a closed set of tools, quote only tenant-
  approved prices, and its replies pass the guardrail pipeline. The
  evaluation suite asserts injected instructions are refused
  (`prompt-injection-*` cases, marked safety-critical).
- **Notification content.** Templates are a closed catalog validated per
  channel, and `assert_safe_variables` refuses transcripts, recordings,
  message bodies, and caller contact details before a provider is
  called.
- **SSRF.** No user-supplied URL is fetched. Provider base URLs come
  from configuration; recording downloads use the URL Twilio returned
  for a recording we own.

## Abuse limits

- Concurrent calls are capped per deployment (`max_concurrent_calls`).
- The media socket applies a bounded, drop-oldest audio queue, a
  per-IP invalid-token limiter, and an idle timeout.
- Bookings are idempotent on a unique key, so a repeated attempt cannot
  create duplicates.
- Notifications claim a unique idempotency key before the provider call,
  so retry storms cannot double-send.
- QStash retries are bounded and terminate in a dead-letter state rather
  than looping.

## Verification

Security behaviour is covered by tests, not just review:

| Property | Test |
|---|---|
| Cross-tenant access returns 404 | `test_authorization.py`, `test_calls_dashboard.py`, `test_client_records.py` |
| Forged Twilio webhook rejected | `test_twilio_webhook.py` |
| Forged QStash delivery rejected | `test_qstash.py` |
| Invalid / replayed WebSocket token rejected | `test_media_ws.py` |
| Signed URL requires authorization, is audited | `test_recordings.py` |
| Role escalation refused (staff vs owner) | `test_authorization.py`, `test_client_records.py`, `test_metrics.py` |
| Admin routes invisible to clients | `test_calls_dashboard.py`, `test_metrics.py` |
| Prompt injection refused | `packages/evals` safety-critical cases |
| Oversized request rejected | `test_security.py` |
| Rate limit enforced per caller | `test_security.py` |
| Secrets never logged or repr'd | `test_security.py`, `test_redaction.py` |

## Dependencies

Dependencies are pinned by `uv.lock` and `package-lock.json`. CI installs
from the lockfile, so a build cannot silently pick up a new transitive
version. Upgrades are a reviewed lockfile diff.

## Reporting

Suspected vulnerabilities go to the platform operator directly. Do not
open a public issue. See [incident-response.md](incident-response.md)
for what happens next.
