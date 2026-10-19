# ClosenAI Personal Model Runtime Architecture

**Version:** V1.0\
**Status:** Engineering Baseline\
**Depends on:** `01`, `02`, `03`, `04`

## 1. Runtime Architecture

``` text
API
 ↓
Application
 ↓
Domain Services
 ↓
Repositories
 ↓
PostgreSQL
```

AI is a controlled inference capability:

``` text
Application
 ↓
AI Orchestrator
 ↓
LLM Provider
 ↓
Typed Candidate
 ↓
Domain Validation
```

AI is never a persistence layer.

## 2. Go Package Structure

``` text
/internal
  /personalmodel/{domain,application,repository,service,policy}
  /evidence/{domain,application,repository,service}
  /pattern/{domain,application,repository,detector}
  /insight/{domain,application,repository,generator}
  /proactive/{domain,application,repository,scheduler}
  /outcome/{domain,application,repository}
  /ai/{client,orchestrator,schemas,prompts}
  /safety/policy
  /events/{domain,publisher}
  /api/http
```

Do not create one giant AI service that owns every domain object.

## 3. Go Domain Model

Core aggregates:

``` text
PersonalModel
PersonalFact
Evidence
Pattern
Insight
Proactive
Outcome
UserFeedback
```

Use typed structs, not raw database JSON.

Recommended value objects:

``` text
Confidence
TimeRange
EvidenceStrength
PatternScore
ModelVersion
ClaimType
TriggerType
```

Example:

``` go
type Confidence float64

func NewConfidence(v float64) (Confidence, error) {
    if v < 0 || v > 1 {
        return 0, ErrInvalidConfidence
    }
    return Confidence(v), nil
}
```

## 4. Aggregate Ownership

**PersonalModel:** model version, model-level transitions and
references.

**Pattern:** lifecycle, score, confidence and evidence references.

**Insight:** user-facing claim, evidence/pattern references and
publication state.

**Proactive:** eligibility, scheduling and delivery state.

**Outcome:** result and domain links.

Raw SourceRecords are not owned by the PersonalModel aggregate.

## 5. Domain Services

Required:

``` text
PersonalModelService
EvidenceService
FactService
PatternService
InsightService
ProactiveService
OutcomeService
```

Policies:

``` text
EvidencePolicy
InsightPolicy
ProactivePolicy
SafetyPolicy
```

## 6. Repository Contracts

Application/domain code depends on interfaces:

``` go
type PatternRepository interface {
    Get(ctx context.Context, id uuid.UUID) (*Pattern, error)
    FindActiveByUser(ctx context.Context, userID uuid.UUID) ([]Pattern, error)
    Save(ctx context.Context, pattern *Pattern) error
}
```

Repository implementations are infrastructure.

## 7. Transaction Manager

Use application-level transaction boundaries:

``` go
type TxManager interface {
    WithinTx(ctx context.Context, fn func(ctx context.Context) error) error
}
```

Do not let individual repositories open independent transactions for one
domain mutation.

## 8. Events / Outbox

Domain events include:

``` text
PatternConfirmed
InsightPublished
ProactiveSent
OutcomeRecorded
PersonalModelUpdated
```

Preferred:

``` text
DB transaction
 ↓
outbox
 ↓
broker
 ↓
consumer
```

Never publish an external event before the DB transaction commits.

If the existing platform already has an Outbox/EventBus, reuse it.

## 9. Source Processing

``` text
POST source
 ↓
SourceRecordService
 ↓
DB commit
 ↓
SourceCreated
 ↓
async worker
 ↓
Extraction
 ↓
EvidenceCandidate
 ↓
Fact/Pattern candidate
 ↓
Domain validation
 ↓
Pattern scoring
 ↓
Insight candidate
 ↓
Insight Gate
```

The initial API request should not wait for the complete AI pipeline.

## 10. AI Orchestrator

Typed operations:

``` go
ExtractObservations(...)
GenerateFactCandidates(...)
GeneratePatternCandidates(...)
GenerateInsightDraft(...)
GenerateProactiveDraft(...)
```

All return schema-validated candidates.

Never parse arbitrary prose as domain state.

## 11. AI Generation Metadata

Record:

``` text
provider
model
model_version
prompt_version
policy_version
detector_version
```

Avoid storing sensitive raw prompts by default. Use hashes/references
and controlled observability.

## 12. Evidence Context Builder

``` text
Canonical DB
 ↓
EvidenceContextBuilder
 ↓
bounded context
 ↓
LLM
```

It must:

-   select relevant records
-   apply privacy policy
-   remove rejected/invalidated knowledge
-   enforce time window
-   enforce token budget
-   include only necessary source content

## 13. Personal Model Snapshot

``` text
Canonical tables
 ↓
SnapshotBuilder
 ↓
personal_model_snapshot
 ↓
AI Context Builder
```

Snapshot is a cache. It must be fully rebuildable from canonical state.

Never let an LLM overwrite the snapshot as authoritative state.

## 14. Incremental Pattern Processing

For a new SourceRecord:

``` text
extract observations
 ↓
find candidate matching Patterns
 ↓
score affected Patterns
 ↓
create new candidate if needed
 ↓
persist only affected state
```

Do not rescan all history for every record.

## 15. Reconciliation

Scheduled:

``` text
daily   stale pattern evaluation
weekly  active-pattern reconciliation
monthly model consistency audit
```

Also run targeted reconciliation after:

``` text
detector version change
policy version change
major source deletion
user correction
```

## 16. Proactive Runtime

``` text
Pattern/Goal/Commitment event
 ↓
Proactive Candidate
 ↓
Eligibility Service
 ↓
Scheduler
 ↓
send provider
 ↓
User
 ↓
Response/Outcome
```

Eligibility is rechecked immediately before sending.

## 17. Outcome Runtime

``` text
User response/action
 ↓
Outcome candidate
 ↓
Domain validation
 ↓
Outcome persisted
 ↓
related Pattern re-evaluation
 ↓
Insight revalidation
 ↓
model version/event
```

## 18. API Boundary

Recommended endpoints:

``` text
GET  /v1/personal-model
GET  /v1/personal-model/facts
GET  /v1/personal-model/patterns
GET  /v1/personal-model/insights

POST /v1/personal-model/facts/:id/feedback
POST /v1/personal-model/patterns/:id/feedback
POST /v1/personal-model/insights/:id/feedback

GET  /v1/personal-model/insights/:id/evidence

GET  /v1/proactives
POST /v1/proactives/:id/respond

POST /v1/outcomes
GET  /v1/outcomes
```

Never expose internal AI candidate APIs to the client.

## 19. Insight API Contract

Example:

``` json
{
  "id": "...",
  "statement": "...",
  "confidence": 0.82,
  "time_range": {
    "from": "...",
    "to": "..."
  },
  "evidence_summary": {
    "count": 4,
    "strength": "strong"
  },
  "feedback_options": [
    "confirm",
    "partially_confirm",
    "reject",
    "outdated"
  ]
}
```

Raw source content is only returned through an authorized
evidence-expansion endpoint.

## 20. Authorization

Never trust `user_id` supplied by the client.

Resolve identity from authenticated context and scope every repository
query to that user.

## 21. Idempotency

Write operations should accept `Idempotency-Key`:

``` text
POST /outcomes
POST /feedback
POST /proactives/:id/respond
```

Async jobs use deterministic keys:

``` text
source_id + pipeline_version
pattern_id + detector_version
insight_id + generation_version
proactive_id + generation_version
```

Retries must not duplicate domain state.

## 22. Concurrency

Use optimistic locking for:

``` text
personal_models
facts
patterns
proactives
```

Conceptually:

``` sql
UPDATE ... WHERE id = $1 AND version = $2;
```

On conflict:

``` text
reload
→ re-evaluate
→ retry
```

Do not globally lock every operation for a user.

## 23. Security / Privacy

Sensitive content must not appear in:

``` text
application logs
metrics labels
trace attributes
error messages
analytics payloads
```

Use IDs/hashes.

Send only minimum required context to AI providers.

## 24. CMS Boundary

CMS may configure:

``` text
prompt templates
policy versions
curriculum content
reflection templates
feature flags
```

CMS must not directly write:

``` text
personal_facts
patterns
insights
outcomes
```

Runtime decides whether content applies to a user.

## 25. Growth Curriculum Boundary

Curriculum consumes validated:

``` text
Patterns
Goals
Outcomes
```

It does not create canonical Facts itself.

``` text
Pattern
 ↓
Curriculum Recommendation
 ↓
User Action
 ↓
Outcome
```

## 26. Life Archive Boundary

Life Archive is a read-only consumer of:

``` text
Source
Facts
Patterns
Insights
Outcomes
```

It must not mutate Personal Model state.

## 27. Data Deletion

Deletion is a domain workflow:

``` text
delete/invalidate source
 ↓
invalidate dependent Evidence
 ↓
recalculate Patterns
 ↓
revalidate Insights
 ↓
cancel affected Proactives
 ↓
rebuild Snapshot
 ↓
emit ModelUpdated
```

Never assume a raw SQL delete is enough.

## 28. DDL / Migration Order

Final migration sequence:

``` text
001 personal_models
002 personal_model_versions
003 source_records
004 existing-goals integration
005 existing-commitments integration
006 evidence
007 personal_facts
008 fact_evidence
009 patterns
010 pattern_evidence
011 insights
012 insight_evidence
013 insight_patterns
014 user_feedback
015 proactives
016 proactive_insights
017 proactive_patterns
018 outcomes
019 outcome_links
020 personal_model_events
021 ai_generation_runs
022 personal_model_snapshots
023 constraints/indexes
024 outbox integration (only if absent)
```

Do not duplicate existing platform tables.

## 29. Go Implementation Order

``` text
Phase 1  value objects + enums
Phase 2  domain aggregates
Phase 3  repositories
Phase 4  transaction manager
Phase 5  EvidenceService
Phase 6  FactService
Phase 7  PatternService
Phase 8  InsightService
Phase 9  ProactiveService
Phase 10 OutcomeService
Phase 11 AI Orchestrator
Phase 12 async workers
Phase 13 API handlers
Phase 14 observability + reconciliation
```

## 30. Test Pyramid

### Unit

``` text
confidence
evidence score
pattern score
state transitions
policy gates
```

### Integration

``` text
repository
transactions
foreign keys
optimistic concurrency
outbox
```

### Pipeline

``` text
Source → Evidence → Pattern → Insight
```

### End-to-End

``` text
Record → Pattern → Proactive → Outcome → Model Update
```

## 31. Runtime State Ownership

  State           Owner
  --------------- ----------------------
  Source          SourceRecordService
  Evidence        EvidenceService
  Fact            FactService
  Pattern         PatternService
  Insight         InsightService
  Proactive       ProactiveService
  Outcome         OutcomeService
  Safety          SafetyPolicy
  Model version   PersonalModelService
  LLM candidate   AI Orchestrator

## 32. Final Runtime

``` text
                    Client
                      ↓
                     API
                      ↓
                 Application
                      ↓
          ┌───────────┴───────────┐
          ↓                       ↓
    Domain Services        AI Orchestrator
          ↓                       ↓
     Repositories             LLM Provider
          ↓                       ↓
      PostgreSQL          Typed Candidates
          ↓                       │
       Events ◄───────────────────┘
          ↓
       Workers
          ↓
 Pattern / Insight / Proactive / Outcome
```

Final invariant:

``` text
User Data
 ↓
Deterministic Domain State
 ↓
Evidence
 ↓
Derived Intelligence
 ↓
User Experience
 ↓
Outcome
 ↓
Domain State Update
```

Never:

``` text
LLM → database → truth
```

This runtime is the implementation boundary for the Personal
Intelligence architecture.
