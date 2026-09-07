## 1. Runtime resource lifecycle

- [x] 1.1 Add Agent-owned/session-closed state and explicit ownership metadata for Runtime Store, Archive, Emitter, and capture manager.
- [x] 1.2 Refactor Runtime Facade setup so session resources initialize once, per-turn context resources reset each chat, and identity mismatches fail closed.
- [x] 1.3 Change `chat()` finalization to flush/finalize/snapshot without closing Agent-owned Store, and add idempotent `Agent.aclose()` with owned-resource cleanup.

## 2. CLI and shared-resource boundaries

- [x] 2.1 Add REPL `try/finally` cleanup and one-shot cleanup wrapper; close resume-owned Store at the CLI owner boundary.
- [x] 2.2 Verify parent/sub-agent and caller-owned Store/Sink/Archive paths never close or replace shared resources.

## 3. Regression tests

- [x] 3.1 Add a deterministic two-chat integration test where the second oversized tool result archives successfully and its Runtime Store metadata is readable.
- [x] 3.2 Add lifecycle ownership tests for Agent close, repeated close, closed-Agent rejection, caller-owned Store preservation, and shared sub-agent resources.
- [x] 3.3 Add Archive failure/reopen tests proving no dangling ref and successful metadata mirror recovery for an existing content-addressed object.

## 4. Validation and acceptance

- [x] 4.1 Run focused lifecycle, artifact, durable-boundary, and remediation integration tests; inspect the protected diff for unrelated changes.
- [x] 4.2 Run the complete Python test suite and `openspec validate --changes --strict`; record results and remaining platform/environment risks.
