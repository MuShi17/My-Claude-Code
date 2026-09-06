## Purpose

在不削弱 Canonical Event 可重建性的前提下复用稳定历史前缀，并只投影当前 Run 新增事件，降低长任务每一步完整 Replay 的重复扫描成本。

## ADDED Requirements

### Requirement: A run reuses a verified prior prefix

The system SHALL materialize the verified history before the active Run once per context epoch and SHALL retain the prefix identity using source high-water, source digest, projection version, and context epoch.

#### Scenario: First step initializes a prefix

- **WHEN** a Run begins with an existing Canonical session history
- **THEN** the system builds one verified prior prefix and records the cursor needed to continue from its high-water

#### Scenario: Same epoch reuses the prefix

- **WHEN** a subsequent provider step adds canonical events without changing the context epoch
- **THEN** the system reuses the prior prefix and does not re-reduce the complete prior ledger

### Requirement: New events are projected as a durable suffix

The system SHALL read only events after the cursor high-water for normal steps, apply them through the same reducer semantics as cold replay, and append the resulting current-turn suffix without fabricating incomplete message groups.

#### Scenario: Tool result extends the suffix

- **WHEN** a model tool call and its durable result are appended after the cursor
- **THEN** the suffix contains one provider-valid assistant tool-use group followed immediately by the corresponding tool-result group

#### Scenario: Multiple calls remain grouped

- **WHEN** one model response contains multiple tool calls
- **THEN** incremental projection preserves them in one assistant group and preserves result ordering and pairing

### Requirement: Incremental and cold replay are equivalent

The system SHALL provide the same provider-neutral effective messages, source digest, and diagnostics when a history is produced incrementally or rebuilt from the Canonical Store from scratch.

#### Scenario: Cold rebuild matches warm state

- **WHEN** the active cursor is discarded and the same event prefix is cold-replayed
- **THEN** provider-neutral messages and projection diagnostics match the warm incremental result

#### Scenario: Checkpoint transition starts a new epoch

- **WHEN** a committed effective-context transition changes the active history
- **THEN** the cursor invalidates the old prefix and establishes a new epoch from the committed effective context

### Requirement: Invalid cursors fail closed and are observable

The system SHALL trigger a controlled cold rebuild when cursor identity, source digest, event ordering, projection version, or context epoch cannot be verified, and SHALL record the reason and bounded read/projection diagnostics.

#### Scenario: Source digest mismatch

- **WHEN** the stored cursor digest does not match the event prefix at its high-water
- **THEN** the system discards the warm cursor, cold-rebuilds from Canonical facts, and records a digest-mismatch rebuild reason

#### Scenario: Projection cache is damaged

- **WHEN** a warm projection cannot be decoded or validated
- **THEN** the system preserves the Canonical Store and rebuilds the projection without sending an unverifiable context

### Requirement: Prefix identity is explicit at the request boundary

The system SHALL expose the shared prefix digest, context epoch, source high-water, read event count, projection duration, and rebuild reason for each materialized provider request.

#### Scenario: Request diagnostics are emitted

- **WHEN** a provider request is materialized from a warm or cold path
- **THEN** diagnostics identify the context epoch and whether the request reused or rebuilt its prior prefix without recording raw request content
