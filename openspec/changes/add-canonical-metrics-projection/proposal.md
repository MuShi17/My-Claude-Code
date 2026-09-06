## Why

The current SessionTracer still acts as an independent runtime record for timing, token, tool, and turn statistics. Once legacy logging is removed, these metrics must remain available as a deterministic projection of Canonical Events rather than disappearing or becoming a second source of truth.

## What Changes

- Add a versioned metrics projection over canonical events and durable tool operations.
- Reconstruct turn/run/session timing, token usage, provider finish, retry, permission, and tool outcome metrics.
- Make metrics snapshots disposable and rebuildable from a canonical high-water and digest.
- Remove the requirement for tracer JSONL as a metrics input.

## Capabilities

### New Capabilities

- `canonical-runtime-metrics`: Rebuildable runtime metrics derived from canonical facts.

### Modified Capabilities

None.

## Impact

Changes projection modules, session summaries, diagnostic output, metrics tests, and the later AgentLogger/SessionTracer removal. It does not add a metrics service or a new external dependency.
