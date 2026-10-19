# ClosenAI Personal Intelligence Runtime V3

This package is the consolidated architecture baseline for the ClosenAI
Personal Intelligence Runtime.

## Architecture Decision

``` text
Go Core Runtime
    = Canonical Truth

Python AI Runtime
    = AI Intelligence

PostgreSQL + pgx + sqlc
    = Canonical Persistence

RabbitMQ
    = Async Transport

Protobuf
    = Cross-language Wire Contract

Outbox
    = Reliable DB → Message Publication

Inbox
    = Consumer Idempotency

Domain Gate
    = Final Canonical Truth Authority
```

## Document Map

  --------------------------------------------------------------------------
  No.                     Document                Purpose
  ----------------------- ----------------------- --------------------------
  00                      Architecture Overview   Entire system architecture

  01                      Personal Model Domain   Domain model baseline
                          Architecture            

  02                      PostgreSQL Schema       Canonical database

  03                      Evidence Pattern        Evidence/pattern/insight
                          Insight Engine          logic

  04                      Proactive Outcome Loop  Proactive and outcome loop

  05                      Personal Model Runtime  Runtime ownership and flow
                          Architecture            

  06                      Components and          Go/Python boundaries
                          Boundaries              

  07                      Go Domain               Executable domain design

  08                      Repository              PostgreSQL + pgx + sqlc

  09                      Service                 Application/domain
                                                  orchestration

  10                      AI Contract             Go ↔ Python candidate
                                                  contract

  11                      Event Contract          Domain event contract

  12                      Python AI Runtime       Python ownership and
                                                  worker model

  13                      Go AI Orchestrator      AI task orchestration

  14                      Messaging Architecture  RabbitMQ topology and
                                                  delivery

  15                      Protobuf Wire Contract  `.proto` and schema
                                                  evolution

  16                      Serialization           Marshal / Unmarshal
                                                  boundary

  17                      Go Python Messaging     End-to-end worker
                          Runtime                 communication

  18                      Outbox Inbox            Reliable delivery
                          Idempotency             semantics

  19                      End-to-End AI Event     Complete example flow
                          Flow                    

  20                      Migration Deployment    Production deployment

  21                      Implementation Runbook  Recommended implementation
                                                  order
  --------------------------------------------------------------------------

## Recommended Implementation Sequence

``` text
Domain
  ↓
PostgreSQL
  ↓
Repository
  ↓
Service
  ↓
AI Contract
  ↓
Event Contract
  ↓
Python AI Runtime
  ↓
RabbitMQ + Protobuf
  ↓
Serialization / Deserialization
  ↓
Outbox / Inbox
  ↓
End-to-End Integration
  ↓
Production Hardening
```

## Core Invariant

``` text
Python Candidate
      ↓
Go Domain Validation
      ↓
Canonical PostgreSQL State
```

Never:

``` text
LLM
  ↓
SQL
  ↓
Truth
```

## Delivery Semantics

The runtime uses:

**at-least-once delivery + idempotent processing**

It does not claim global exactly-once execution.

## Package Scope

This package is the architecture/document baseline. The next engineering
phase is to implement the Go Domain → Repository → Service layers
against PostgreSQL + pgx + sqlc, then freeze the concrete AI/Event
protobuf contracts from those actual domain APIs.
