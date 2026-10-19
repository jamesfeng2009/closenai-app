# ClosenAI End-to-End AI Event Flow

## 1. Scenario

A user records a new life event.

The system should extract observations, identify a potential recurring
pattern, and eventually produce a trusted insight.

## 2. Step 1 --- User Input

``` text
Client
  ↓
POST /source-records
  ↓
Go API
```

Go authenticates the user and creates a SourceRecord.

## 3. Step 2 --- Canonical Transaction

``` text
BEGIN
  SourceRecord
  Outbox(SourceRecordCreated)
COMMIT
```

At this point the user data is durable even if AI is unavailable.

## 4. Step 3 --- Event Publication

Outbox worker:

``` text
Outbox row
  ↓
Event protobuf
  ↓
Marshal
  ↓
RabbitMQ
```

## 5. Step 4 --- Python Observation Extraction

Python:

``` text
Deserialize
  ↓
Extract observations
  ↓
Link evidence
  ↓
Generate fact/pattern candidates
```

Candidate messages are serialized and published.

## 6. Step 5 --- Go Candidate Acceptance

Go:

``` text
Candidate
  ↓
Deserialize
  ↓
Schema validation
  ↓
Inbox
  ↓
Authorization
  ↓
Evidence validation
  ↓
Domain Service
```

## 7. Step 6 --- Pattern Scoring

The domain calculates:

``` text
EvidenceStrength
EvidenceDiversity
RecurrenceStrength
TemporalCoherence
Recency
UserConfirmation
```

Then calculates the Pattern Score according to the established engine.

## 8. Step 7 --- Lifecycle

The domain moves the pattern through states such as:

``` text
candidate
→ observed
→ supported
→ active consideration
```

Confirmation remains a user-level semantic action unless policy
explicitly permits otherwise.

## 9. Step 8 --- Outbox

If canonical state changed:

``` text
PatternCreated / PatternUpdated
        ↓
Outbox
```

This event can trigger an insight-draft task.

## 10. Step 9 --- Insight Draft

Python receives bounded evidence context.

It produces:

``` text
InsightDraft
```

The draft is not automatically published.

## 11. Step 10 --- Insight Gate

Go validates:

-   evidence
-   claim type
-   confidence
-   policy
-   freshness
-   safety

Only then can the insight become user-visible.

## 12. Step 11 --- Proactive Follow-Up

If the pattern/insight qualifies:

``` text
Trigger
 ↓
Relevance
 ↓
Confidence
 ↓
Safety
 ↓
Timing
 ↓
Frequency
 ↓
Proactive Candidate
```

Python may draft wording. Go owns eligibility and delivery state.

## 13. Step 12 --- Outcome

The user later reports an outcome.

``` text
Outcome
 ↓
Evidence
 ↓
Pattern update
 ↓
Model version
 ↓
Future proactive decision
```

This closes the compounding loop.

## 14. Full Chain

``` text
User
 ↓
SourceRecord
 ↓
Outbox
 ↓
RabbitMQ
 ↓
Python AI
 ↓
Candidate
 ↓
RabbitMQ
 ↓
Go Inbox
 ↓
Domain Gate
 ↓
Canonical State
 ↓
Outbox
 ↓
RabbitMQ
 ↓
Python Insight Draft
 ↓
Go Insight Gate
 ↓
Proactive
 ↓
User
 ↓
Outcome
 ↓
Personal Model
```

## 15. Core Product Invariant

> The system gets smarter over time because validated evidence and
> outcomes accumulate in the canonical Personal Model.
