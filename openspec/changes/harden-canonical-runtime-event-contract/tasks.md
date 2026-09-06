## 1. Contract and fixtures

- [x] 1.1 Define the strict opening payload with route, configuration, root, source, and lineage, and verify valid/invalid fixture tests cover every required field.
- [x] 1.2 Add invocation-local `event_seq` to the canonical storage model while retaining session ordinal, and verify store-owned sequence allocation with concurrent append tests.
- [x] 1.3 Add operation, step, provider-event, artifact, and continuation refs, and verify round-trip serialization preserves them.

## 2. Invariants

- [x] 2.1 Remove legacy normalization/default identity inference and verify legacy-shaped payloads fail with a structured validation error.
- [x] 2.2 Enforce exact duplicate idempotency and conflict rejection, and verify both paths in SQLite tests.
- [x] 2.3 Enforce one terminal event at the immutable ledger tail and verify terminal race, late append, and finalize failure tests.
- [x] 2.4 Verify partial snapshots remain bounded and partial events are excluded from replay inputs.

## 3. Verification

- [x] 3.1 Run the change's strict OpenSpec validation and the focused runtime event/store test suite.
- [x] 3.2 Record the resulting schema digest and any compatibility-breaking fixture updates in the new batch Item 01.
