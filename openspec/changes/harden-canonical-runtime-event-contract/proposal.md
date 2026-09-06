## Why

The current Canonical RuntimeEvent implementation still accepts migration-era payloads and records an empty invocation opening. Maka demonstrates that the opening fact, per-invocation order, replay metadata, and terminal invariants must be explicit before a canonical-only runtime can safely remove compatibility paths.

## What Changes

- **BREAKING** Make the canonical event envelope strict and reject legacy-shaped payloads.
- Add a frozen `invocation_opened` content contract containing route, configuration, root, source, and lineage.
- Add store-owned per-invocation `event_seq` while retaining the session-wide append ordinal.
- Add operation, step, provider-event, artifact, and continuation references needed for replay and recovery.
- Enforce exact idempotency, sequence integrity, partial filtering, and a single terminal ledger tail.

## Capabilities

### New Capabilities

- `canonical-runtime-event-contract`: Strict event identity, ordering, opening, visibility, and terminal behavior.

### Modified Capabilities

None.

## Impact

Changes the RuntimeEvent domain model, SQLite schema/store, lifecycle emitters, replay fixtures, recovery checks, and event validation tests. This is a prerequisite for removing legacy logger and session compatibility code.
