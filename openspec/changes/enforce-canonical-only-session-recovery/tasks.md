## 1. Session projection

- [x] 1.1 Define canonical-derived snapshot metadata with projection version, high-water, and digest, and verify snapshot delete/rebuild tests.
- [x] 1.2 Restrict session enumeration and latest selection to canonical session-scoped databases, and verify legacy files are ignored.

## 2. CLI and recovery

- [x] 2.1 Remove legacy session/flat JSON/old-root database fallback from list/latest/resume/one-shot, and verify isolated HOME CLI smoke.
- [x] 2.2 Add fail-closed diagnostics for corrupt database, future schema, identity mismatch, and event sequence gaps, and verify source preservation.
- [x] 2.3 Implement conservative stale-run recovery and new continuation boundary without automatic uncertain-tool execution, and verify operation-state fixtures.
- [x] 2.4 Remove canonical authority, rollback, and approval CLI branches after canonical-only behavior is proven, and verify help/argument tests.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and focused session, recovery, and CLI tests.
- [x] 3.2 Record the breaking old-session accessibility boundary and evidence in the new batch Item 05.
