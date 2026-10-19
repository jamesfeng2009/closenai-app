# ClosenAI Go Personal Model Domain

## 1. Goal

Turn the Personal Model from a skeleton into an executable domain layer.

The domain must contain behavior, invariants and lifecycle transitions.
It must not depend on PostgreSQL, RabbitMQ, Protobuf or an LLM.

## 2. Aggregate Ownership

### PersonalModel

Owns:

-   model identity
-   user ownership
-   model version references
-   high-level lifecycle metadata

### PersonalFact

Owns:

-   fact identity
-   semantic content
-   fact type
-   confidence
-   source/evidence relationships
-   lifecycle

### Pattern

Owns:

-   pattern identity
-   type
-   evidence links
-   score
-   lifecycle
-   confirmation/rejection
-   detector metadata

### Insight

Owns:

-   insight identity
-   claim type
-   evidence links
-   pattern links
-   confidence
-   publication state
-   user feedback state

### Proactive

Owns:

-   trigger
-   eligibility
-   scheduling
-   delivery
-   response
-   freshness

### Outcome

Owns:

-   outcome type
-   observed result
-   linked goal/commitment/proactive
-   evidence
-   timestamp

## 3. Domain Methods

Illustrative behavior:

``` go
pattern.RecalculateScore(input ScoreInput)
pattern.RecalculateLifecycle()
pattern.Confirm()
pattern.Reject(reason)
pattern.MarkExpired()
```

The exact Go signatures can evolve; the invariant is that lifecycle
transitions are domain behavior, not arbitrary repository updates.

## 4. Candidate Boundary

AI candidate:

``` text
Candidate
  ↓
Domain Service
  ↓
Aggregate construction / reconciliation
  ↓
Domain invariant validation
  ↓
Score calculation
  ↓
Lifecycle transition
  ↓
Persistence
```

Never:

``` text
LLM output
  ↓
UPDATE patterns
```

## 5. Value Objects

Recommended value objects:

-   Confidence
-   EvidenceStrength
-   PatternScore
-   TimeRange
-   ModelVersion
-   ClaimType
-   TriggerType
-   SourceReference
-   CorrelationID
-   MessageID

Value objects should validate their own local constraints.

## 6. Domain Errors

Examples:

``` text
ErrUnauthorizedModel
ErrInvalidEvidence
ErrInvalidLifecycleTransition
ErrInsufficientEvidence
ErrUnsupportedClaimType
ErrCandidateExpired
ErrDuplicateFeedback
ErrInvalidOwnership
```

Infrastructure-specific errors must be translated at application
boundaries.

## 7. Domain Independence

The domain package must not import:

-   pgx
-   sqlc generated packages
-   RabbitMQ clients
-   protobuf generated packages
-   LLM SDKs
-   HTTP frameworks

Adapters translate infrastructure representations into domain inputs.

## 8. Candidate Acceptance Contract

The application service should expose a semantic operation such as:

``` text
AcceptFactCandidate
AcceptPatternCandidate
AcceptInsightCandidate
AcceptProactiveCandidate
AcceptOutcomeCandidate
```

A generic `AcceptCandidate` dispatcher can exist, but domain-specific
validation must remain explicit.

## 9. Final Rule

> The repository persists domain decisions; it does not make domain
> decisions.
