## Context

See `proposal.md` for motivation. The event sink validates transition structure and recomputes persisted digests, and SQLite has a combined compaction checkpoint/event path. The Agent still builds lightweight and full candidates from mutable working state without first applying them to a validated effective-context copy.

## Goals / Non-Goals

**Goals:**

- Reuse one transition validator for full reset and lightweight replacement candidates.
- Validate exact event identity, source boundary, final normalized values, and provider-safe grouping before store writes.
- Make source changes between read and commit observable and controlled.
- Preserve the last committed context on every persistence failure.

**Non-Goals:**

- Do not make canonical event append mutable or retroactively repair bad historical facts.
- Do not add a distributed transaction protocol for non-SQLite sinks.
- Do not silently recover invalid historical transitions by guessing targets.

## Decisions

### 1. Validate a pure candidate before writing

Add a context-transition validation helper that receives the active neutral messages, source records/high-water, and candidate transition. It clones the messages, resolves replacements by exact `runtime_event_id`, checks optional `target_call_id`, applies each replacement once, validates tool groups, and returns the resulting effective context/digest. The helper has no store side effects.

### 2. Final persisted representation is the digest input

Candidate construction first performs normalization, redaction, bounding, and source-ID preservation. Only then are replacement/result digests computed. The emitter may validate and recompute persisted metadata as a defensive boundary, but it MUST not change digest-covered values afterward.

### 3. Store enforces the source boundary

The authoritative SQLite append method runs in one transaction and rechecks current high-water/source digest before inserting checkpoint plus activation event. Lightweight transitions use an equivalent source-bound append path or a store-level compare-and-append API. A conflict rolls back and signals the Agent to regenerate.

### 4. Activation follows commit

Agent keeps the previous cursor/epoch/provider arrays until the store operation returns success. Then it refreshes from canonical replay. All failures set controlled runtime failure and avoid sending the uncommitted candidate.

### 5. Replay remains strict

Cold and incremental replay share exact-ID validation. A committed invalid transition yields an error diagnostic that the Agent escalates; it never falls back to the legacy logger or to an unscoped call-ID match.

## Risks / Trade-offs

- [Risk] Candidate validation duplicates some reducer work → accept the bounded copy cost because it prevents durable unreplayable state; record validation timing in diagnostics.
- [Risk] Source conflict causes repeated compaction work → retry only with a fresh high-water and bounded attempt count, then fail controlled.
- [Risk] Non-transactional sinks cannot provide SQLite atomicity → they must preserve the prior active context and report activation failure rather than claim equivalence.

## Migration Plan

1. Extract pure candidate application/validation and add negative tests.
2. Integrate validation into lightweight and full compaction callers after final normalization.
3. Add compare-and-append source checks to store activation APIs.
4. Verify failure injection, chained compaction, and recovery behavior.

Rollback is to stop creating new transitions; existing invalid transitions remain diagnosable and are not silently bypassed.
