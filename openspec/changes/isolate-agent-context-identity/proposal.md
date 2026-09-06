## Why

Child Agents currently share a session and SQLite ledger while deriving their invocation identity from a local ask counter. The first child can therefore collide with a parent invocation, and a projection that reads the shared ledger can expose child history to the parent model. Identity and context ownership must be explicit before more replay and compaction work is accepted.

## What Changes

- Generate independent run and invocation identities for every root, child, fork-skill, and resumed execution.
- Add a stable model-context identity that groups only the events visible to one Agent context.
- Scope model replay, incremental cursors, memory injection, and context transitions to the active context.
- Preserve an explicit parent run/context relationship for traceability without implicitly inheriting parent model history.
- Make child startup and recovery safe when parent and child share one Canonical Store.

## Capabilities

### New Capabilities

- `context-scoped-agent-runtime`: Defines unique execution identities and isolated model-context ownership for root and child Agents.

### Modified Capabilities

None.

## Impact

- Affects `RunContext`, `RuntimeEvent`, SQLite event filtering/indexes, Agent runtime startup, child-agent and fork-skill construction, and both replay implementations.
- Adds context identity to canonical event metadata/envelopes and replay diagnostics.
- Existing parent/child trace relationships remain queryable; child events are no longer model-visible to the parent unless explicitly materialized as a parent tool result.
- No provider API or legacy-log compatibility behavior is changed by this change.
