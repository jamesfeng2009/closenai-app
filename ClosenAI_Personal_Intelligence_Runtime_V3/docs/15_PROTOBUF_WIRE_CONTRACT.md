# ClosenAI Protobuf Wire Contract

## 1. Source of Truth

The `.proto` files are the cross-language contract.

Recommended location:

``` text
contracts/proto/
  closenai_ai.proto
  closenai_events.proto
  closenai_common.proto
```

## 2. Generated Artifacts

Go:

``` text
protoc
  ↓
*.pb.go
```

Python:

``` text
protoc
  ↓
*_pb2.py
```

Generated code must not be hand-edited.

## 3. Common Envelope

A common envelope can contain:

``` protobuf
message MessageEnvelope {
  string message_id = 1;
  string message_type = 2;
  string schema_version = 3;
  google.protobuf.Timestamp occurred_at = 4;
  string producer = 5;
  string correlation_id = 6;
  string causation_id = 7;
  string user_id = 8;
  string personal_model_id = 9;
  uint32 attempt = 10;
  bytes payload = 11;
}
```

The actual project may instead use typed top-level messages with a
common metadata message. The important point is consistent traceability
and versioning.

## 4. AI Messages

Recommended families:

``` text
AIObservationTask
AIFactCandidate
AIPatternCandidate
AIInsightDraft
AIProactiveDraft
AIOutcomeCandidate
```

## 5. Event Messages

Recommended families:

``` text
SourceRecordCreated
EvidenceAccepted
FactCreated
PatternCreated
PatternUpdated
InsightPublished
ProactiveScheduled
OutcomeRecorded
UserFeedbackRecorded
```

## 6. Field Number Rules

Never reuse a field number.

When removing:

``` protobuf
reserved 7;
reserved "old_field";
```

## 7. Compatibility

Prefer additive changes:

``` text
v1:
id, type, confidence

v1.1:
id, type, confidence, detector_version
```

Do not change:

``` text
string confidence
```

into:

``` text
double confidence
```

under the same field number.

## 8. Enum Rules

Add new enum values rather than renumbering existing values.

Consumers should tolerate unknown enum values.

## 9. Timestamp Rules

Use protobuf Timestamp for cross-language timestamps.

Store canonical UTC in PostgreSQL.

Convert to local time only at presentation/scheduling boundaries.

## 10. IDs

Use explicit string/UUID-compatible identifiers.

Do not allow language-specific binary ID representations to leak into
the business contract without a deliberate schema decision.

## 11. Contract Registry

A small registry should document:

``` text
message type
schema version
producer
consumer
routing key
compatibility status
```

## 12. Contract Tests

Every release should run:

``` text
Go encode → Python decode
Python encode → Go decode
old message → new consumer
new additive message → old tolerant consumer
invalid message → rejection path
```
