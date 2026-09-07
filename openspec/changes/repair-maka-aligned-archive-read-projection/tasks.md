## 1. Archive capability and bounded reader

- [x] 1.1 Extend ArtifactArchive with bounded page metadata/content reads using validated artifact refs, safe text offsets, and response limits.
- [x] 1.2 Add ToolResultArchiveCapability with session/lineage scope, reader/decoder binding, capability derivation, and stable archive error mapping.
- [x] 1.3 Add the capability-owned ArchiveRead tool schema and handler for inspect, read, and the frozen query subset; reject path inputs, invalid ranges, and limit overflow.
- [x] 1.4 Add archive unit tests for page boundaries, Unicode offsets, hash/size mismatch, missing refs, scope denial, closed stores, and bounded error messages.

## 2. Provider-visible result projection

- [x] 2.1 Add a provider-neutral archived-result view that derives read instructions for legacy refs and distinguishes canonical result, provider projection, and terminal projection.
- [x] 2.2 Implement first-use projection: materialize the complete safe artifact when the effective Provider request budget permits it.
- [x] 2.3 Implement capacity rescue and stale projection with the frozen preview, truncation, ref, next_offset, read hint, and minimal-envelope fallback semantics.
- [x] 2.4 Integrate the neutral projection into live tool loops and cold replay/resume/compaction reconstruction without rewriting canonical events.
- [x] 2.5 Wire equivalent ArchiveRead tool definitions, tool-result formatting, and provider request behavior for Anthropic and OpenAI-compatible backends.

## 3. Parent-child capability and lifecycle

- [x] 3.1 Derive and pass the archive capability to skill-fork and agent sub-agents while keeping ordinary child allowlists unchanged and preventing name collisions.
- [x] 3.2 Ensure parent/child artifact refs retain session/lineage authorization and child completion cannot close caller-owned archive or runtime store resources.
- [x] 3.3 Add close barriers and failure handling so pending Provider projection/ArchiveRead operations settle before Agent-owned resources close and closed stores fail closed.

## 4. Terminal projection

- [x] 4.1 Separate terminal formatting from Provider projection for small, capacity-rescued, stale, ArchiveRead, and error outcomes.
- [x] 4.2 Add terminal/CLI regression coverage for bounded previews, actionable hints, redaction, no implicit full hydration, and distinct lifecycle errors.

## 5. Provider, sub-agent, and security regression tests

- [x] 5.1 Add fake-consumer tests proving both providers receive complete first-use content when it fits and a recoverable prefix preview only when it does not.
- [x] 5.2 Add stale round-trip tests proving placeholder -> ArchiveRead inspect/read -> bounded page in the next Provider context.
- [x] 5.3 Add parent/child, scope, allowlist, double-bounding, redaction, and archive-failure regression tests using the real local archive store.
- [x] 5.4 Preserve and rerun existing Canonical Event, Compaction, Resume, permission, and side-effect tool tests.

## 6. Validation and writeback

- [x] 6.1 Run focused archive/provider/sub-agent/lifecycle tests and close failures without weakening the frozen contract.
- [x] 6.2 Run the complete Python test suite, OpenSpec strict validation, and git diff --check; inspect the final protected diff.
- [x] 6.3 Write actual files, commands, evidence levels, residual risks, Gate state, and worktree disposition back to the batch, task card, and Items; leave delivery actions unauthorized.
