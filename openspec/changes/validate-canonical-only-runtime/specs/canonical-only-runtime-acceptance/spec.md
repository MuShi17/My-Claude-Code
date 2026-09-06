## Purpose

Defines reproducible integration and boundary checks proving that the runtime remains safe and rebuildable after legacy logging and compatibility paths are removed.

## ADDED Requirements

### Requirement: Canonical-only behavior is integration-verified

Acceptance MUST cover canonical event ordering and terminal invariants, durable tool boundaries, Provider replay, Session/Metrics/Compaction rebuild, Recovery, privacy, and CLI behavior in one reproducible matrix.

#### Scenario: Full canonical-only smoke

- **WHEN** the acceptance matrix runs in an isolated HOME with deterministic providers
- **THEN** all supported flows complete using canonical storage and produce no legacy runtime files

### Requirement: Durability failures are observable

Acceptance MUST demonstrate that append failure, finalize failure, corruption, identity conflict, sequence gap, and uncertain tool outcome fail or classify in the controlled manner defined by the canonical contract.

#### Scenario: Canonical storage is unavailable

- **WHEN** a required canonical write fails during a model or tool boundary
- **THEN** the active run does not continue as successful and no legacy route is attempted

### Requirement: Legacy boundary is proven

Acceptance MUST prove that forbidden legacy symbols and paths are absent from runtime behavior and that pre-existing legacy files remain byte-for-byte unchanged.

#### Scenario: Legacy files coexist with canonical data

- **WHEN** old logs and session files are placed beside a new canonical database
- **THEN** list/latest/resume ignore them, do not mutate them, and use only canonical data

### Requirement: Evidence boundaries are explicit

Acceptance results MUST identify whether each claim is based on a fixture, local integration, real provider, multi-process, or production environment, and MUST NOT promote weaker evidence to a production claim.

#### Scenario: Only local fake-provider evidence exists

- **WHEN** no real provider or production deployment was exercised
- **THEN** the report records those scopes as unverified rather than declaring production readiness
