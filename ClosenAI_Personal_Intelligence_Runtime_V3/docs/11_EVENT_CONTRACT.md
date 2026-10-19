# ClosenAI Event Contract

## 1. Purpose

Events describe durable facts about system state transitions.

They are not the same thing as AI candidates.

``` text
AI Contract
  = work request / candidate

Event Contract
  = state transition / durable domain event
```

## 2. Event Envelope

Recommended fields:

``` text
message_id
message_type
schema_version
occurred_at
producer
correlation_id
causation_id
user_id
personal_model_id
attempt
payload
```

## 3. Domain Event Examples

V1:

``` text
SourceRecordCreated
EvidenceAccepted
FactCreated
FactUpdated
PatternCreated
PatternRecalculated
PatternConfirmed
PatternRejected
InsightPublished
ProactiveScheduled
ProactiveDelivered
OutcomeRecorded
UserFeedbackRecorded
PersonalModelVersionCreated
```

## 4. Event Semantics

Events should describe something that already happened.

Bad:

``` text
PatternShouldBeCreated
```

Good:

``` text
PatternCreated
```

Commands/candidate requests are requests to perform work. Events are
records of completed transitions.

## 5. Correlation and Causation

Example:

``` text
source_created
  message_id = A

AI pattern request
  causation_id = A
  correlation_id = C

AI pattern candidate
  causation_id = request_id
  correlation_id = C

pattern_created
  causation_id = candidate_message_id
  correlation_id = C
```

This allows an entire workflow to be traced.

## 6. Producer Identity

Examples:

``` text
go-core
python-ai
migration-tool
admin-console
```

Only trusted producers may emit canonical domain events.

## 7. Event Payload

The payload should be:

-   explicit
-   versioned
-   minimal
-   deterministic
-   safe to persist

Avoid embedding giant prompt transcripts or raw model context into every
event.

## 8. Schema Evolution

Rules:

1.  Add fields instead of changing meaning.
2.  Never reuse field numbers.
3.  Never silently change a field's semantic meaning.
4.  Reserve removed field numbers.
5.  Keep consumers tolerant of unknown fields.
6.  Maintain compatibility tests.

## 9. Event Retention

Canonical audit/event retention should be defined separately from
RabbitMQ retention.

RabbitMQ is transport, not the canonical event store.

## 10. Event vs Outbox

An outbox row is a delivery mechanism.

An event is the business message.

The outbox may contain:

``` text
event_type
payload
message_id
status
attempt_count
published_at
last_error
```

After publication, RabbitMQ carries the serialized event.
