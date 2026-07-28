# Running the dashboard locally with real data

For screenshots, demos, or working on the UI. Takes about ten minutes,
most of it waiting for a Clerk account.

Uses a real Clerk development instance — the same authentication path
production uses, not a bypass. Nothing here weakens the auth model:
authorization still comes entirely from database rows, which is why
step 5 exists.

---

## What you need

- Docker (for PostgreSQL)
- A Clerk account — free, and the dev instance needs no card

---

## 1. Database

```bash
docker run -d --name receptionist-dev-pg -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=receptionist -p 5433:5432 postgres:16-alpine
```

```bash
export DATABASE_DIRECT_URL="postgresql+asyncpg://postgres:dev@localhost:5433/receptionist"
```

```bash
uv run alembic upgrade head
```

## 2. Seed a business, and give it a history

```bash
uv run python -m ai_database.seed && uv run python -m ai_database.seed_activity
```

The first creates Harbor Plumbing (Demo) — services, prices, hours,
service area. The second gives it four calls with transcripts, a
confirmed booking, a message, an escalated emergency, guardrail events,
and usage.

Without the second, every page renders its empty state and the
screenshots show nothing. Both are idempotent.

## 3. Clerk

In the [Clerk dashboard](https://dashboard.clerk.com):

1. Create an application. Enable **Organizations** — the platform maps
   one Clerk organisation to one tenant, so without it there is no
   `org_id` in the token and sign-in cannot resolve a tenant.
2. Create an organisation inside it (name it anything).
3. From **API keys**, copy the publishable key, the secret key, and the
   **Frontend API URL** (it looks like
   `https://xxx-yyy-00.clerk.accounts.dev`) — that is the JWT issuer.
4. Note your **user id** (`user_...`) and **organisation id**
   (`org_...`) from the Users and Organizations pages.

## 4. Configuration

`apps/dashboard/.env.local` — never commit this:

```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_APP_URL=http://localhost:3000
```

`.env` at the repository root, for the API:

```
DATABASE_URL=postgresql+asyncpg://postgres:dev@localhost:5433/receptionist
CLERK_SECRET_KEY=sk_test_...
CLERK_JWT_ISSUER=https://xxx-yyy-00.clerk.accounts.dev
CLERK_JWT_AUDIENCE=
```

Leave `CLERK_JWT_AUDIENCE` empty unless you set a custom audience in
Clerk. The JWKS URL is derived from the issuer.

Both files are gitignored. Put the keys in the files yourself rather
than pasting them into a terminal that gets logged.

## 5. Link Clerk to the tenant

**This is the step people miss.** A valid Clerk token proves who you
are; it says nothing about which business you may see. That mapping
lives in the database, so a fresh organisation gets a 403 until you
create it:

```bash
uv run python -m ai_database.link_clerk --org-id org_YOURORG --user-id user_YOURUSER
```

This points the demo tenant at your organisation and activates you on it
as the owner. Idempotent.

For the **admin console** (`/admin/*`), add your user id to the API's
`PLATFORM_ADMIN_USER_IDS` instead — platform admin is not a tenant
membership, deliberately.

## 6. Run

Two terminals.

```bash
uv run uvicorn api.main:app --port 8000
```

```bash
cd apps/dashboard && npm run dev
```

Open http://localhost:3000, sign in through Clerk, and **switch to your
organisation** using the org switcher — a session with no active
organisation has no `org_id` and will be refused.

You should land on the overview with four calls, one booking, one
message, and real latency figures.

---

## If it doesn't work

| What you see | Cause |
|---|---|
| "No organization membership in session" | No active org — use the org switcher |
| "Organization is not linked to a tenant" | Step 5 not run, or a different `org_id` |
| "Membership is not active" | Step 5 ran with a different `user_id` |
| Pages load but everything is empty | `seed_activity` not run |
| Every page shows an error card | API not running, or `API_BASE_URL` wrong |
| 401 on every request | Issuer mismatch between Clerk and `CLERK_JWT_ISSUER` |
| `/admin` returns 404 | Correct — admin is invisible without `PLATFORM_ADMIN_USER_IDS` |

The API logs the reason for each refusal, so start there rather than
guessing.

---

## Worth knowing

The demo data is invented, for a fictional business. It never feeds a
number shown to a real client — the metrics endpoints compute from real
rows only, and show nothing rather than a placeholder when there is
nothing to show.

To start over:

```bash
docker rm -f receptionist-dev-pg
```
