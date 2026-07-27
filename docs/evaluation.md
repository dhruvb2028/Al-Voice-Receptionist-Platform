# Evaluation

Unit tests prove a function does what it was written to do. They cannot
tell you whether the receptionist quotes a price it shouldn't when a
caller pushes hard. That needs a different instrument.

## Design decisions

**Cases are version-controlled YAML.** A behaviour change becomes a
reviewable diff rather than a number moving on a dashboard.

**Cases are scripted, not sampled.** The conversation and the model's
replies are fixed, so the suite runs in CI without provider keys and the
same case yields the same verdict on every machine. A red result means
behaviour changed, not that a model sampled differently.

This is a real trade: scripted cases pin known behaviour and cannot
discover an unimagined failure mode. Sampling against a live model finds
novel failures but is non-deterministic, needs credentials, and cannot
gate a merge. The suite exists to prevent regressions; live sampling
belongs in the launch plan's fifty test calls.

**The schema is strict.** `extra="forbid"`, so a typo like
`expected_toolz` is a load error. A misspelled expectation silently
weakens the suite, which is worse than no expectation.

## Coverage

64 cases across all 32 required scenarios — normal bookings, changed
dates and services, unavailable slots, out-of-hours and out-of-area
requests, price questions, discount pressure, three emergency types,
angry callers, human requests, wrong numbers, spam, silence, rambling,
degraded transcripts, missing details, calendar timeout and revocation,
duplicate bookings, SMS and transfer failures, LLM and TTS timeouts,
interruption, maximum duration, prompt injection, and cross-tenant
probing.

`--require-coverage` fails if any required scenario has no case, so
deleting a case is a visible failure rather than a quiet gap.

## Assertions

Twelve families:

| Check | Safety |
|---|---|
| Required tool ran | |
| Forbidden tool did not | ✔ |
| Tool order held | |
| Outcome matched | |
| Booking state correct | |
| **No premature confirmation** | ✔ |
| **No unapproved price** | ✔ |
| No forbidden claim | ✔ |
| Escalation correct, including message fallback | ✔ |
| Message created with the right urgency | |
| Tenant isolation held | ✔ |
| Latency within target | |

Safety findings gate CI on their own, because a suite can add passing
cases and raise its average while quietly breaking a safety property.

## Every assertion is tested against a failure

The most important property of this harness:

> An assertion that never fires looks exactly like an assertion that
> always passes.

So each check has a deliberately-broken run proving it catches the
violation, and a clean run proving it does not false-alarm. Without
that, a green suite means nothing.

## Reports

JSON and self-contained HTML: pass rate, task completion, booking
success, containment, false and missed escalations, hallucinated prices,
premature confirmations, p50/p95 latency, estimated cost.

Regression is judged **per case** against a previous run's JSON, not on
the headline rate — a suite whose average improves while a safety case
flips from pass to fail has regressed.

## Running

```bash
uv run python -m ai_evals.cli --require-coverage
```

```bash
uv run python -m ai_evals.cli --baseline eval-results/report.json --out eval-results
```

| Exit | Meaning |
|---|---|
| `0` | All passed |
| `1` | Quality failures, no safety regression |
| `2` | **Safety regression or safety-critical failure** |

The distinct code lets a pipeline treat safety as a hard gate while
tolerating an in-progress quality failure.

## Adding a case

Any class of bug that reaches production gets a case, so the same
failure cannot return quietly. Add a YAML file, run with
`--require-coverage`, and confirm it fails before the fix and passes
after — a case that never failed proves nothing.
