# Secret rotation

How to change a credential without dropping a call.

## Principles

**Rotation is a deploy, not a live edit.** Secrets resolve once per
process and are cached; changing a Secret Manager version has no effect
until the service restarts. That is intentional — a value that changes
mid-request is worse than one that changes at a known moment.

**Overlap where the provider allows it.** Anything that verifies a
signature must accept the old and new keys simultaneously, or in-flight
requests fail during the switch.

## Routine rotation

Every 90 days, and immediately on suspicion of exposure.

1. Create the new version:

```bash
printf '%s' "$NEW_VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-
```

2. Deploy so services pick it up. Config references
   `sm://.../versions/latest`, so a redeploy is enough.

3. Verify the new value is in use — a real call for provider keys, a
   dashboard sign-in for Clerk.

4. Disable (do not destroy) the old version:

```bash
gcloud secrets versions disable SECRET_NAME/versions/<OLD>
```

Disabling is reversible for a week or so; destroying is not. Destroy only
after the new value is proven in production.

## Per-secret notes

**QStash signing keys.** The verifier already accepts current *and* next
keys, so rotation drops no jobs. Rotate in Upstash, update both
`QSTASH_CURRENT_SIGNING_KEY` and `QSTASH_NEXT_SIGNING_KEY`, deploy.

**Twilio auth token.** Twilio supports a secondary token. Promote the
secondary, update the secret, deploy, then retire the primary. Rotating
without the overlap means every webhook fails signature verification for
the duration of the deploy — which, for a voice platform, means calls do
not connect.

**Groq / Deepgram / Cartesia.** Issue a new key, deploy, then revoke the
old one. These are used outbound only, so there is no verification window
to worry about; the only risk is revoking before the deploy lands.

**Clerk.** Rotating the secret key invalidates nothing already issued;
rotating JWKS signing keys does. Clerk manages the overlap — check their
dashboard before forcing it.

**`CALL_TOKEN_SIGNING_KEY`.** Rotating invalidates every unused media
token. Tokens are single-use and short-lived, so the blast radius is
calls connecting in the same few seconds. Rotate during a quiet window.

**R2 access keys.** Create the new pair, deploy, verify a recording
uploads and a signed URL resolves, then delete the old pair.

## Encryption keys — read before touching

`DATA_ENCRYPTION_KEY` and `LOOKUP_HASH_KEY` are **not** routine
rotations.

Rotating the data key without re-encrypting makes every existing
encrypted column permanently unreadable. Ciphertext is versioned (`v1:`)
specifically so a future key can coexist with old rows, but that requires
a re-encryption migration that does not exist yet. Until it does:

- Do not rotate the data key.
- If it is exposed, that is a SEV1 incident, and re-encryption must be
  written and rehearsed on a Neon branch before it runs anywhere near
  production.

Rotating the **hash** key invalidates every lookup hash — caller history
search stops matching until hashes are recomputed. Also not routine.

Both keys must be backed up outside the database. See
[backup-restore.md](backup-restore.md).

## After a suspected exposure

1. Rotate immediately; do not wait for a maintenance window.
2. Revoke the old credential at the provider, not just in configuration.
3. Check provider logs for use you did not make.
4. Follow [incident-response.md](incident-response.md) — an exposed
   credential is at least SEV2, and one with data access is SEV1.
5. Record what leaked, how, and what changed so it cannot recur.

## Verification

After any rotation:

- [ ] All three services healthy
- [ ] One real call: answers, transcribes, replies, books
- [ ] A post-call job completes (proves QStash verification)
- [ ] A notification sends
- [ ] A recording uploads and its signed URL resolves
- [ ] No authentication errors in Sentry
