## 1. Pure transition validation

- [x] 1.1 Add candidate application/validation over a copied neutral context with exact event-ID lookup and optional call-ID confirmation; verify missing/duplicate-ID negative cases
- [x] 1.2 Validate final normalized values, replacement/result digests, epoch, source high-water/digest, and complete tool groups; verify long/redacted values use the persisted representation

## 2. Agent integration

- [x] 2.1 Validate lightweight compression candidates before emitting an activation event; verify stale target and source-conflict candidates are rejected without append
- [x] 2.2 Validate full-compaction candidates after group-safe source preservation and before checkpoint construction/activation; verify summary and retained tail are replayable
- [x] 2.3 Keep previous cursor/epoch/provider arrays until durable success and route failures to controlled run termination; verify provider is never called with an uncommitted candidate

## 3. Store atomicity and recovery

- [x] 3.1 Add compare-and-append source checks to SQLite checkpoint/transition and lightweight transition paths; verify transaction rollback leaves no half activation
- [x] 3.2 Surface committed transition corruption as a diagnostic/error without legacy fallback; verify restart behavior targets the last valid state

## 4. Verification

- [x] 4.1 Add failure-injection and chained full→lightweight→restart tests, including duplicate call IDs across runs; verify no invalid transition is durable
- [x] 4.2 Run focused transition tests, OpenSpec strict validation, and the full `py313` suite; verify all checks pass
