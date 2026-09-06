## Purpose

Defines a strict, self-describing Canonical RuntimeEvent contract that preserves runtime facts, replay boundaries, and terminal lifecycle integrity without migration-era inference.

## ADDED Requirements

### Requirement: Invocation opening is a complete immutable fact

Each invocation MUST begin with one non-partial opening event that records its route, execution configuration, root/source, and applicable lineage before provider or tool dispatch.

#### Scenario: Provider dispatch follows a complete opening

- **WHEN** a new invocation starts and a provider request is about to be sent
- **THEN** the canonical ledger contains the complete opening fact before the provider request or any tool dispatch begins

#### Scenario: Opening data is incomplete

- **WHEN** an opening event lacks required route, configuration, root, or source data
- **THEN** the runtime rejects the invocation before dispatch and reports a structured validation failure

### Requirement: Event ordering is store-owned

The canonical store MUST assign a strictly increasing per-invocation event sequence and a session-wide append ordinal; callers MUST NOT choose or overwrite either value.

#### Scenario: Concurrent appends

- **WHEN** two events for one invocation are appended concurrently
- **THEN** the store commits unique, strictly ordered sequence values without allowing a caller-supplied value to win

### Requirement: Canonical event identity is exactly idempotent

Appending an event with an existing ID MUST be idempotent only when its canonical payload and identity match; a same-ID payload conflict MUST be rejected.

#### Scenario: Exact retry

- **WHEN** the same event is submitted again after a successful append
- **THEN** the store returns an idempotent result and creates no second ledger row

#### Scenario: Conflicting retry

- **WHEN** an existing event ID is submitted with a different canonical payload
- **THEN** the store rejects the append and preserves the original event

### Requirement: Terminal event seals the invocation

An invocation MUST have at most one terminal event, and a terminal event MUST be the immutable ledger tail; appends after sealing MUST fail closed.

#### Scenario: Terminal race

- **WHEN** competing writers attempt to finalize one invocation
- **THEN** exactly one terminal event is durably committed and later finalization attempts are classified as sealed or exact idempotent retries

### Requirement: Partial events are not model history

Partial streaming content MUST remain bounded presentation state and MUST be excluded from Model Replay input.

#### Scenario: Partial stream is replayed

- **WHEN** a projection builds the next provider context while a partial snapshot exists
- **THEN** the context contains only finalized canonical content and no partial token delta
