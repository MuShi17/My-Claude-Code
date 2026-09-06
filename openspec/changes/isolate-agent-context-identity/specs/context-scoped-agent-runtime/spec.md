## Purpose

Provides collision-free execution identities and explicit model-context ownership so concurrent or nested Agents can share a Canonical Store without sharing model history accidentally.

## ADDED Requirements

### Requirement: Every execution has a unique invocation identity

The system SHALL assign every root turn, child Agent, forked skill, retry attempt, and resumed turn an invocation identity that is unique within the shared Canonical Store. A local ask counter MUST NOT be the sole source of invocation identity.

#### Scenario: Child Agent starts after parent events exist

- **WHEN** a child Agent is started from a parent that has already emitted events in the same session and store
- **THEN** the child opening event is accepted without an invocation collision and is linked to the parent run

#### Scenario: Two children start from one parent

- **WHEN** two child Agents are started during one parent turn
- **THEN** both children receive distinct run and invocation identities and both execution traces can be appended

#### Scenario: A resumed session starts a new turn

- **WHEN** a canonical session is restored and the next turn begins
- **THEN** the resumed turn receives a fresh run and invocation identity while remaining associated with the restored session

### Requirement: Model context ownership is explicit

Each canonical event that can affect model history SHALL belong to a stable context identity. A model replay request MUST consume only events belonging to the active context plus an explicitly recorded inherited prefix, if one exists.

#### Scenario: Parent refreshes after child completion

- **WHEN** a child emits user, model, tool, or compaction events into the shared store
- **THEN** refreshing the parent model context does not include the child history, child memory injection, or child compaction state

#### Scenario: Child refreshes its own context

- **WHEN** a child refreshes its provider context
- **THEN** it sees its own canonical history and any explicitly configured inherited prefix, but not unrelated sibling context events

### Requirement: Parent-child relationships remain traceable without implicit history merging

The system SHALL retain parent run/context linkage for Session and Trace projections. A child result SHALL become parent model history only through the explicit parent tool-call/result boundary.

#### Scenario: Child returns a result

- **WHEN** a child Agent completes through the `agent` tool or a forked skill
- **THEN** the parent can observe the child result as its tool result while the child's intermediate model history remains outside the parent's replay context

#### Scenario: Child compaction occurs

- **WHEN** a child commits a context transition or full compaction
- **THEN** that transition changes only the child's effective context and does not alter the parent's epoch, cursor, or messages

### Requirement: Replay state is context-bound

Incremental cursors, memory-injection idempotency, compaction transitions, and recovery snapshots SHALL carry or derive the active context identity and MUST reject cross-context application.

#### Scenario: Cursor is reused for another context

- **WHEN** a cursor created for one context is presented with events from another context
- **THEN** the cursor is invalidated and a context-scoped rebuild or controlled error occurs before a provider request is sent

#### Scenario: Recovery restores a child context

- **WHEN** a child context is recovered from canonical state
- **THEN** only that child context's effective messages and committed transitions are restored
