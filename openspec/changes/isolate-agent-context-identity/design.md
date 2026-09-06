## Context

See `proposal.md` for the motivation. `RuntimeEvent` already carries session, run, invocation, and parent-run coordinates, but `Agent._start_runtime_facade()` currently derives invocation IDs from `session_id + ask_count`. Store reads and both model projections have no context ownership filter, so a shared ledger is treated as one model conversation.

## Goals / Non-Goals

**Goals:**

- Add a durable context coordinate that is stable for a root Agent session and unique for each child/fork context.
- Allocate run and invocation IDs from the identity factory at runtime start, including recovery and retry paths.
- Filter cold and incremental model replay by context while preserving the full event ledger for trace/session projections.
- Keep parent-child trace edges and explicit result-return semantics.

**Non-Goals:**

- Do not merge child history into the parent model prompt implicitly.
- Do not redesign the parent tool protocol or child result text.
- Do not delete historical events or introduce distributed locking.

## Decisions

### 1. Context identity is a first-class runtime coordinate

Extend `RunContext` and the canonical event envelope with `context_id` and an optional `parent_context_id`. The root context is stable for the Agent's session; `IdentityFactory.child_context()` creates a fresh context for each child/fork. A context index and read filter are added to SQLite. Storing the coordinate in the envelope is preferred over an unindexed metadata convention because every projection must make the same ownership decision.

### 2. Runtime identities are generated, not counter-derived

At run startup, use `IdentityFactory.run_id()` and `IdentityFactory.invocation_id()` unless an explicitly supplied identity is being resumed under a validated run. The ask counter remains a display/turn number only. Explicit IDs are validated against the store before opening an event, and child construction receives a generated context and parent linkage.

### 3. Child contexts start with no implicit parent prompt

The child receives its own system prompt and user task. If a future caller needs parent history, it must pass an explicit immutable inherited-prefix high-water/context binding; the default child path does not do so. The parent sees the child only through the existing agent/skill tool result event.

### 4. Projection filtering is applied before reduction

`read_event_records(context_id=...)` is the canonical boundary for model replay. `ModelReplayProjection` and `IncrementalModelReplayCursor` carry the context ID and reject records outside it. Session/Trace projections continue to read the unfiltered ledger so execution-tree observability is preserved.

### 5. Recovery validates identity ownership

Session snapshots and replay diagnostics persist the active context ID, high-water, epoch, and source digest. A missing or mismatched context is a controlled recovery error, not a fallback to the whole session ledger.

## Risks / Trade-offs

- [Risk] Existing fixtures construct `RunContext` without a context ID → provide a deterministic root default for test/legacy construction while all Agent-created contexts pass an explicit ID.
- [Risk] A context filter could hide useful child trace data → keep Session/Trace queries unfiltered and expose parent linkage separately.
- [Risk] Reusing an explicit run ID after a crash could still collide → validate run state and generate a fresh invocation for each new opening event.

## Migration Plan

1. Add context coordinates and store migration/index/filter support.
2. Generate and propagate context identity through root, child, fork-skill, retry, and resume paths.
3. Scope model replay and cursor initialization; leave Session/Trace ledger reads unchanged.
4. Add child isolation and recovery regression tests.

Rollback is a code-level cursor fallback to context-scoped cold replay; no canonical event deletion or force migration is permitted.
