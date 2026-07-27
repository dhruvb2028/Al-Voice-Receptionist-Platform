# Call Lifecycle

**Related:** [System architecture](system-architecture.md) · [Security boundaries](security-boundaries.md)

This document traces every path a conversation can take through the platform: the inbound phone call, the browser test call, the booking transaction, the human-transfer fallback, and post-call processing.

---

## 1. Inbound call flow

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant TW as Twilio
    participant API as API service
    participant V as Voice service
    participant R as Redis
    participant DB as PostgreSQL

    C->>TW: Dials tenant number
    TW->>API: POST /webhooks/twilio/voice (signed)
    API->>API: Verify X-Twilio-Signature on raw body
    API->>DB: Resolve tenant by To number
    alt Number unknown or tenant not active
        API-->>TW: TwiML: neutral unavailable message, hangup
    else Tenant active
        API->>DB: INSERT calls row (status=ringing)
        API->>API: Mint signed call token (call_sid, tenant_id, short TTL)
        API-->>TW: TwiML: announcement + Connect Stream wss://voice/ws?token=...
        TW->>V: WebSocket open with call token
        V->>V: Validate token (signature, TTL, single use)
        V->>R: Load resolved tenant config
        alt Config cache miss
            V->>DB: Fallback read + rewarm cache
        end
        V->>DB: UPDATE calls SET status=in_progress
        V-->>TW: Greeting audio (Cartesia stream)
        loop Conversation turns
            TW->>V: Caller audio frames
            V->>V: Deepgram STT + endpointing
            V->>V: State machine + Groq + tools
            V-->>TW: Response audio frames
        end
        TW->>V: Stop event (hangup)
        V->>DB: Finalize call row (outcome, duration, telemetry)
        V->>V: Publish call.ended to QStash
    end
```

**Turn loop detail** — within each turn:

1. Inbound µ-law frames stream to Deepgram; interim transcripts accumulate.
2. Endpointing signals end of caller turn (semantic endpoint + silence threshold).
3. The state machine assembles a bounded context: system prompt, tenant facts, conversation state, recent turns, older-turn summary.
4. Groq generates either a short spoken response or a tool call. Tool calls execute with hard timeouts; if a tool exceeds ~400 ms a filler phrase covers the wait.
5. Response text streams to Cartesia; audio chunks stream to Twilio as they arrive — no stage buffers a complete payload before forwarding.
6. If the caller speaks during playback (barge-in): TTS is cancelled, the Twilio buffer is cleared, and the new utterance becomes the active turn.
7. Turn record (text, tool calls, per-stage latency) is queued for async persistence — the write never blocks audio.

---

## 2. Browser test-call flow

The simulator runs the **identical** conversation engine — state machine, tools, guardrails — with text I/O replacing audio and telephony.

```mermaid
sequenceDiagram
    autonumber
    participant A as Admin browser
    participant WEB as Dashboard
    participant API as API service
    participant ENG as Conversation engine (shared)
    participant DB as PostgreSQL

    A->>WEB: Open test console, select tenant
    WEB->>API: POST /admin/simulator/sessions (JWT, admin role)
    API->>API: Authorize platform-admin role
    API->>DB: Load tenant config
    API->>ENG: Create session (simulated transport, sandbox tools)
    API-->>WEB: session_id + greeting text
    loop Text turns
        A->>WEB: Type caller message
        WEB->>API: POST /admin/simulator/sessions/:id/turns
        API->>ENG: Process turn (state machine, Groq, tools)
        ENG-->>API: Reply + state transition + tool calls + guardrail decisions
        API-->>WEB: Full turn trace
        WEB-->>A: Reply plus inspector: state, entities, tools, guardrails
    end
    A->>WEB: End session
    WEB->>API: POST .../end
    API->>DB: Persist simulator session (flagged is_simulation=true)
```

**Sandboxing rules:** simulator sessions are marked `is_simulation` and excluded from usage, notifications, and client dashboards. Booking tools run in dry-run mode against live calendars unless the admin explicitly enables real writes for an end-to-end test. Saved sessions become regression scenarios for the evaluation harness.

---

## 3. Booking transaction

The invariant: **the caller hears a confirmation only after both the database row and the calendar event exist.**

```mermaid
sequenceDiagram
    autonumber
    participant V as Voice service
    participant DB as PostgreSQL
    participant GC as Google Calendar

    V->>V: Caller accepts offered slot
    V->>V: Build idempotency key (call_id + slot + service)
    V->>DB: BEGIN
    V->>DB: INSERT booking (unique on tenant_id+idempotency_key, overlap constraint)
    alt Constraint violation (duplicate or overlap)
        DB-->>V: Insert rejected
        V->>DB: ROLLBACK
        V-->>V: Offer alternative slot or take message
    else Row inserted
        V->>GC: Create calendar event (timeout-bounded)
        alt Calendar write fails
            GC-->>V: Error
            V->>DB: ROLLBACK
            V-->>V: Apologize, offer alternative or take message
        else Event created
            GC-->>V: event_id
            V->>DB: UPDATE booking SET calendar_event_id
            V->>DB: COMMIT
            V-->>V: NOW speak confirmation to caller
        end
    end
```

**Race handling:** two concurrent callers targeting the same slot are serialized by the database overlap constraint — exactly one insert succeeds; the loser's transaction rolls back and that caller is offered the next slot. The calendar is treated as a replica of the bookings table, not the authority. A committed booking whose calendar write later proves inconsistent is repaired by the worker's reconciliation sweep, in favour of the database.

**Idempotency:** replaying the same booking request (retry, duplicate tool call) hits the unique key and returns the existing booking rather than creating a second one.

---

## 4. Human-transfer fallback

Every escalation path converges on the same ladder: **transfer → message → alert.** No call ends in silence.

```mermaid
flowchart TD
    T1[Emergency detected] --> ESC
    T2[Caller asks for a human] --> ESC
    T3[Two failed intent resolutions] --> ESC
    T4[Unhandled voice-path error] --> ESC
    ESC[Escalation initiated] --> ANN[Tell caller they are being transferred]
    ANN --> DIAL[Dial tenant transfer number via Twilio]
    DIAL --> Q{Answered within timeout?}
    Q -->|Yes| BRIDGE[Bridge caller and human]
    BRIDGE --> OUT1[Outcome: transferred - AI leaves the call]
    Q -->|No answer / busy / invalid| APOL[Apologize, offer to take a message]
    APOL --> CAP[Capture: name, number, reason, urgency]
    CAP --> SAVE{Message persisted?}
    SAVE -->|Yes| OUT2[Outcome: message_taken, urgent flag if emergency]
    SAVE -->|No| OUT3[Outcome: failed]
    OUT2 --> NOTIF[Immediate notification to tenant]
    OUT3 --> ALERT[Sentry alert + admin failure queue]
```

Emergency escalations skip pleasantries: the transfer is announced and dialled immediately, and the notification fires even when the transfer succeeds.

---

## 5. Post-call processing

Triggered by `call.ended` on QStash. At-least-once delivery; every step is idempotent on `call_id`.

```mermaid
flowchart TD
    QS[QStash delivers call.ended] --> VER[Verify QStash signature]
    VER --> IDEM{Already processed?}
    IDEM -->|Yes| ACK[Ack, done]
    IDEM -->|No| TRANS[Assemble final transcript from turn records]
    TRANS --> SUM[LLM summary + outcome classification]
    SUM --> FLAGS[Quality flags: dead air, tool failures, repeated escalation]
    FLAGS --> REC{Recording enabled?}
    REC -->|Yes| FETCH[Fetch recording from Twilio]
    FETCH --> STORE[Store in R2 tenant-scoped path]
    STORE --> DEL[Delete provider copy]
    REC -->|No| USAGE
    DEL --> USAGE[Write usage rows: minutes, cost estimate]
    USAGE --> NOTIF[Dispatch notifications: booking / message / escalation]
    NOTIF --> DONE[Mark call processed - record immutable]
    DONE --> ACK2[Ack QStash]

    FETCH -->|Twilio error| RETRY[Fail step, QStash redelivers with backoff]
    SUM -->|LLM error| RETRY
    NOTIF -->|Resend error| RETRY
```

A step that fails after QStash's retry budget lands in the dead-letter queue and the admin failure review; the call record remains available with whatever stages completed. Reconciliation sweeps (scheduled via QStash) catch calls stuck in `in_progress`, orphaned provider recordings, and undelivered notifications.

---

## 6. Call outcomes

Every call terminates in exactly one outcome, set by the voice service (or reconciliation, for crashed calls):

| Outcome | Meaning | Triggers notification |
|---|---|---|
| `booked` | Booking committed and confirmed | Yes — booking confirmation |
| `message_taken` | Structured message persisted | Yes — message alert (urgent variant for emergencies) |
| `transferred` | Human bridge succeeded | Only if emergency-flagged |
| `answered_inquiry` | Question answered from tenant data, no follow-up needed | No |
| `caller_hangup` | Caller left before an outcome | No |
| `failed` | Application/provider failure ended the call | Admin alert |
