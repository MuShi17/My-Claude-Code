## Context

The existing runtime emits `function_call`, `tool_dispatch`, and `function_response`, but the dispatch payload is only a tool name and argument digest. Maka separates the semantic event from an operational tool journal so a lost outcome does not destroy correlation or invite unsafe replay.

## Goals / Non-Goals

**Goals:**

- Make every tool operation traceable from provider call through terminal result.
- Atomically reserve the dispatch boundary and keep unresolved operations diagnosable.
- Keep artifact references bounded and content-addressed.

**Non-Goals:**

- Exactly-once external side effects.
- Automatic retry or reconciliation of an uncertain side effect.
- Importing Maka's workspace mutation authority protocol.

## Decisions

1. Store operation records in the same SQLite database as RuntimeEvents. The operation record is operational state; the RuntimeEvent remains the semantic fact source.
2. Use `operation_id` plus provider tool call ID and canonical argument hash as the stable correlation tuple. Conflicts are rejected.
3. Commit durable dispatch before calling the tool implementation. Commit outcome only after execution returns or a controlled failure is known.
4. Represent an interrupted operation as `outcome_unknown`; recovery reports it and starts no automatic side effect.
5. Archive oversized output before committing its result reference, with hash, byte size, and archive identity.

## Risks / Trade-offs

- [Crash window] A process can die after external side effect and before outcome → mark uncertain and require a new user-directed invocation.
- [Two records] Operational state and semantic events can diverge → write them in one store transaction where possible and validate cross references during recovery.
- [Large output] Artifact I/O can fail after tool execution → preserve the tool failure/unknown state and never fabricate a complete result.

## Migration Plan

Add operation fields and tests while the existing boundary is still present. Update Agent Loop callers, then remove the old logger/shadow paths only after operation recovery tests pass.
