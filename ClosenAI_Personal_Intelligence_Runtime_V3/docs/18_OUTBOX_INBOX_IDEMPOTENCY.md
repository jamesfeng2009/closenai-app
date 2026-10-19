# ClosenAI Outbox / Inbox / Idempotency

## 1. Why Both Are Required

Outbox solves:

> How do we reliably publish a message after a database transaction?

Inbox solves:

> How do we safely process the same message more than once?

They solve opposite sides of the delivery problem.

## 2. Outbox

Canonical transaction:

``` text
BEGIN
  write canonical state
  insert outbox(message_id, payload, ...)
COMMIT
```

The outbox worker later publishes the message.

## 3. Outbox Fields

Recommended:

``` text
id
message_id
message_type
schema_version
routing_key
payload
status
attempt_count
available_at
published_at
last_error
created_at
updated_at
```

## 4. Outbox Status

Example:

``` text
pending
publishing
published
failed
dead_lettered
```

A production implementation may simplify these states.

## 5. Claiming Work

Workers should safely claim batches using PostgreSQL mechanisms such as:

``` sql
FOR UPDATE SKIP LOCKED
```

This permits multiple workers without duplicate active claims.

## 6. Publisher Confirm

Do not mark an outbox message `published` until RabbitMQ confirms the
publication.

## 7. Inbox

Recommended fields:

``` text
message_id
message_type
consumer
received_at
processed_at
status
error_code
attempt_count
```

Unique key:

``` text
(message_id, consumer)
```

This permits the same message to be consumed by multiple independent
consumers while remaining idempotent per consumer.

## 8. Inbox Transaction

``` text
BEGIN

INSERT inbox(message_id, consumer)

if duplicate:
    COMMIT
    ACK
    return

process domain mutation

insert outbox

COMMIT

ACK
```

## 9. Crash Scenarios

### Crash before commit

No canonical state is committed. Message can be retried.

### Crash after commit before ACK

Message is redelivered. Inbox sees duplicate. Consumer safely ACKs.

### Crash during RabbitMQ publish

Outbox remains pending/uncertain. Publisher confirms and retry strategy
resolve the state.

## 10. Idempotency Keys

Use stable message IDs generated at the logical operation boundary.

Do not generate a new message ID on every retry.

Retries represent the same logical message.

## 11. Business Idempotency

Message idempotency is not enough for semantic duplicates.

Example:

Two different messages may represent the same pattern candidate.

Use a domain fingerprint:

``` text
pattern_type
subject
normalized description
time scope
model/detector version
```

Then reconcile at the domain layer.

## 12. Exactly-Once Clarification

The system provides:

> at-least-once delivery + idempotent processing

It does not promise global exactly-once execution.

## 13. Cleanup

Inbox/outbox retention must be designed.

Do not delete idempotency records so aggressively that an old redelivery
can create duplicate effects.
