## Purpose

Provides deterministic, bounded, and rebuildable runtime metrics derived from Canonical Events so observability survives the removal of the independent tracer log.

## ADDED Requirements

### Requirement: Metrics are canonical-derived

Runtime metrics MUST be computed from canonical events and durable tool operation state, not from legacy tracer files or an independent lifecycle record.

#### Scenario: Tracer snapshot is absent

- **WHEN** a metrics projection runs without a tracer snapshot or tracer JSONL
- **THEN** it computes the supported metrics from canonical events and operation state

### Requirement: Metrics preserve lifecycle facts

The projection MUST expose bounded values for turn/run/session timing, first-token availability, token usage, provider finish, retry, permission, tool outcome, and terminal status.

#### Scenario: Complete run is projected

- **WHEN** canonical events contain provider, permission, tool, usage, and terminal facts
- **THEN** the metrics projection reports those facts with stable names and source high-water

#### Scenario: A metric source is absent

- **WHEN** canonical history does not contain enough information for a metric
- **THEN** the projection reports an explicit unavailable value and does not infer a fabricated duration or count

### Requirement: Metrics snapshots are rebuildable

Any materialized metrics snapshot MUST include a projection version, source high-water, and source digest, and MUST be safe to delete and regenerate.

#### Scenario: Snapshot is deleted

- **WHEN** the metrics snapshot is removed and the canonical event ledger remains intact
- **THEN** a new projection rebuilds equivalent metrics from the recorded source boundary

### Requirement: Metrics do not expose raw sensitive content

Metrics MUST use bounded classifications, identifiers, hashes, and counts rather than raw provider requests, prompts, tool arguments, or results.

#### Scenario: Sensitive marker is present in a request

- **WHEN** a canonical reference points to a request containing a secret marker
- **THEN** the metrics output contains no raw marker and remains usable for timing and outcome reporting
