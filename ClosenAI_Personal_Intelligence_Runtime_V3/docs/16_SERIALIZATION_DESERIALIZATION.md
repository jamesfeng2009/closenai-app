# ClosenAI Serialization and Deserialization

## 1. Goal

Make serialization/deserialization an explicit infrastructure layer.

Business logic must never depend on raw AMQP bytes.

## 2. End-to-End

``` text
Go Domain/Application Object
        ↓
protobuf mapping
        ↓
Generated protobuf message
        ↓
Marshal
        ↓
[]byte
        ↓
RabbitMQ
        ↓
[]byte
        ↓
Unmarshal
        ↓
Generated protobuf message
        ↓
Contract validation
        ↓
Application input
        ↓
Domain
```

## 3. Go Producer

Logical implementation:

``` go
msg := &pb.AIPatternCandidate{
    CandidateId: candidate.ID.String(),
    // ...
}

body, err := proto.Marshal(msg)
if err != nil {
    return err
}

return publisher.Publish(ctx, PublishRequest{
    RoutingKey: "candidate.pattern",
    ContentType: "application/protobuf",
    MessageID: candidate.MessageID,
    Body: body,
})
```

The mapping from domain candidate to protobuf must be explicit.

## 4. Go Consumer

Logical implementation:

``` go
var msg pb.AIPatternCandidate

if err := proto.Unmarshal(body, &msg); err != nil {
    return PermanentContractError(err)
}

candidate, err := mapper.ToDomainCandidate(msg)
if err != nil {
    return PermanentContractError(err)
}

return service.AcceptPatternCandidate(ctx, candidate)
```

## 5. Python Producer

Logical implementation:

``` python
message = AIPatternCandidate(
    candidate_id=candidate.id,
    # ...
)

body = message.SerializeToString()

publisher.publish(
    body=body,
    content_type="application/protobuf",
    message_id=message_id,
)
```

## 6. Python Consumer

Logical implementation:

``` python
message = AIPatternTask()
message.ParseFromString(body)

task = mapper.to_task(message)
result = ai_service.run(task)
```

## 7. Mapping Layer

Do not expose generated protobuf types directly to the domain.

Prefer:

``` text
protobuf DTO
   ↓ mapper
application DTO
   ↓
domain
```

This protects the domain from wire-schema churn.

## 8. Validation Layers

There are three separate validations:

### Wire validation

Can the bytes be decoded?

### Contract validation

Does the decoded message satisfy schema-level semantics?

### Domain validation

Is the requested state transition legal?

## 9. Error Classification

### Serialization error

Usually permanent.

### Unknown schema version

Usually permanent until software supporting it is deployed.

### Missing referenced evidence

May be transient if eventual consistency is expected.

### Domain rejection

Permanent business rejection; record it and do not blindly retry.

## 10. Payload Security

Never deserialize untrusted payloads into executable objects.

Protobuf parsing is data decoding, not code execution, but downstream
mappings must still validate:

-   lengths
-   counts
-   IDs
-   enum values
-   timestamps
-   nested object sizes

## 11. Observability

Record metrics such as:

``` text
protobuf_encode_total
protobuf_decode_total
protobuf_decode_error_total
contract_validation_failure_total
message_bytes
serialization_latency
```

Do not log raw personal payloads by default.
