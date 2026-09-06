## Why

The current transition sink validates schema and digests but does not validate that a proposed replacement or reset can actually apply to the active effective context before writing the canonical activation event. A missing target or stale source can therefore become durable history that every later recovery must reject.

## What Changes

- Validate full-compaction and lightweight transitions against the current context projection before persistence.
- Require exact source event identity and optional call-identity confirmation; never fall back to an unscoped call ID.
- Verify source high-water/digest, complete tool pairing, replacement digests, and final effective-context digest before activation.
- Make checkpoint plus reset transition atomic and prevent the new context from being sent until commit succeeds.
- Add failure-injection tests proving invalid candidates leave no new unusable transition behind.

## Capabilities

### New Capabilities

- `transition-activation-safety`: Defines pre-commit validation and atomic activation guarantees for effective-context transitions.

### Modified Capabilities

None.

## Impact

- Affects transition construction and validation in `context_transition.py`, compaction/lightweight callers in `agent.py`, event sink/store atomic paths, and replay diagnostics.
- Tightens failure behavior: invalid or stale candidates fail the current run in a controlled way and preserve the prior effective context.
- Does not mutate append-only source events or weaken canonical corruption handling.
