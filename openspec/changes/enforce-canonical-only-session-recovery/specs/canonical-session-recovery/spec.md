## Purpose

Provides canonical-derived session listing, resume, and conservative recovery so a damaged or incomplete canonical ledger is diagnosed and never silently replaced by a legacy data source.

## ADDED Requirements

### Requirement: Session views are canonical-derived

Session listing, latest-session selection, and resume MUST read canonical runtime events and MAY use only snapshots that identify their canonical source boundary.

#### Scenario: Snapshot is missing

- **WHEN** a canonical database exists but its session snapshot is missing
- **THEN** the session view rebuilds from canonical events and records the resulting high-water and digest

### Requirement: CLI operations are canonical-only

List, latest, resume, and one-shot session behavior MUST NOT read legacy JSON, legacy JSONL, or an old-root runtime database.

#### Scenario: Only a legacy session exists

- **WHEN** the filesystem contains a legacy session but no canonical session
- **THEN** the CLI reports no canonical session and does not load or continue the legacy data

### Requirement: Corruption fails closed

Canonical corruption, future schema, identity mismatch, or event sequence gap MUST preserve the source database, return a diagnostic, and prevent silent fallback.

#### Scenario: Canonical database is corrupt

- **WHEN** resume cannot validate the canonical ledger
- **THEN** resume fails with a bounded diagnostic and does not select a legacy source

### Requirement: Recovery does not replay uncertain side effects

Recovery MUST conservatively classify stale or incomplete invocations and MUST NOT automatically execute a tool whose durable dispatch has no durable outcome.

#### Scenario: Resume finds dispatch without outcome

- **WHEN** the last canonical operation is a durable dispatch without a response
- **THEN** recovery reports an uncertain operation and requires an explicit new invocation for retry
