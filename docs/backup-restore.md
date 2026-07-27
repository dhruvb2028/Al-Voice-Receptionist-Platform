# Backup and restore

What is backed up, what is not, and how to get data back.

## What exists

| Asset | Backup | Recovery point | Recovery time |
|---|---|---|---|
| PostgreSQL (Neon) | Continuous WAL, point-in-time restore | Seconds | Minutes |
| Recordings (R2) | None beyond the object itself | — | Not recoverable once deleted |
| Configuration | Versioned in `config_versions` | Every change | Instant rollback |
| Code | Git + tagged container images | Every commit | Minutes |
| Secrets | Secret Manager versions | Every rotation | Instant |

Two of these deserve emphasis.

**Recordings have no second copy.** Once the provider copy is deleted and
retention removes the R2 object, the audio is gone. That is the intent —
retention is a privacy commitment, not an accident — but it means a
recording deleted on schedule cannot be recovered for a dispute. A legal
hold, set before the window expires, is the only mechanism that preserves
one.

**Encryption keys are the real single point of failure.** Losing
`DATA_ENCRYPTION_KEY` makes every encrypted column permanently
unreadable: caller numbers, addresses, message bodies, OAuth tokens. A
database backup does not help, because the ciphertext restores intact and
undecryptable. Key material must be backed up separately from the
database and verified recoverable.

## Point-in-time restore

Neon restores into a **new branch**. Never restore in place over a live
database.

1. Identify the target timestamp — usually just before the bad
   deploy or the destructive statement.

2. Create a branch at that point:

```bash
neonctl branches create --project-id "$NEON_PROJECT_ID" --name restore-check --parent production --timestamp "2026-07-28T14:05:00Z"
```

3. **Verify before cutting over.** Connect to the branch and confirm the
   data you expected is there and the data you did not is not:

```bash
neonctl connection-string restore-check --project-id "$NEON_PROJECT_ID"
```

4. Decide what to cut over. Whole-database promotion loses everything
   written after the restore point — real bookings, real messages. Very
   often the better move is to **copy the specific rows** out of the
   restore branch into the live database and leave production running.

5. If promoting the branch, pause affected tenants first so no call is
   answered against a database about to be swapped.

6. Delete the branch when finished; branches cost money and hold data.

## Partial recovery

Usually the right answer. A bad migration or a mistaken delete affects
one table, and everything else has moved on since. Restore to a branch,
then copy back only what was lost, checking foreign keys as you go —
`calls`, `usage_records`, and `audit_logs` use `RESTRICT`, so a partial
copy in the wrong order will be refused rather than corrupt anything.

## Rehearsal

An untested restore is not a backup. Rehearse:

- Before the first client goes live
- After any material schema change
- Quarterly thereafter

The rehearsal is: create a branch from an hour ago, connect, run the
schema test suite against it, confirm a known call record is present,
delete the branch. If that takes longer than fifteen minutes, the process
needs fixing before an incident makes it urgent.

## What is deliberately not recoverable

- Recordings past their retention window (privacy commitment)
- Notifications already sent (there is no unsend)
- Audit log content for purged records — the fact of the deletion is
  retained, the content is not

These are design decisions, not gaps. Say so plainly to a client asking
for them back.
