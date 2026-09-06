## Why

The current tool boundary records a dispatch event and an outcome but does not provide the stable operation identity and recovery state modeled by Maka. Without that identity, a crash after dispatch cannot distinguish a safe retry from an uncertain side effect.

## What Changes

- Add a durable tool operation identity with provider call ID, canonical argument hash, and recovery mode.
- Persist dispatch and outcome state in the same runtime storage boundary as canonical events.
- Enforce dispatch-before-side-effect and explicit uncertain outcomes.
- Preserve artifact-first handling for large results.
- **BREAKING** Do not automatically replay a tool whose dispatch is durable but whose outcome is unknown.

## Capabilities

### New Capabilities

- `durable-tool-operations`: Durable tool operation identity, state transitions, and recovery semantics.

### Modified Capabilities

None.

## Impact

Changes tool lifecycle recording, SQLite schema and transactions, recovery diagnostics, artifact references, and tool boundary tests. It is consumed by Agent Loop integration and canonical-only recovery.
