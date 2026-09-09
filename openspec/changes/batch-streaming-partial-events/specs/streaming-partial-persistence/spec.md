## ADDED Requirements

### Requirement: Streaming partials SHALL use a mutable snapshot boundary

Provider streaming text, thinking and tool-argument deltas emitted by `ModelCallRecorder` SHALL be treated as incomplete observations, not as immutable canonical ledger facts. The first delta of each stream SHALL synchronously create or update a recoverable streaming partial snapshot. The recorder-driven SQLite path SHALL NOT append those partials to `runtime_events`; the existing direct `SQLiteRuntimeStore.append(partial_event)` API SHALL remain backward compatible for callers that explicitly use it.

#### Scenario: The first text delta establishes a durable anchor

- **WHEN** a started recorder receives its first text delta for an invocation and the SQLite transaction succeeds
- **THEN** `runtime_stream_partials` contains one snapshot for that stream before the recorder returns, `runtime_events` contains no recorder partial row, and the snapshot identifies the session, run, invocation and attempt

#### Scenario: Direct legacy partial append remains available

- **WHEN** a caller directly passes a valid `partial=true` `RuntimeEvent` to `SQLiteRuntimeStore.append()`
- **THEN** the store applies the pre-existing append contract and does not reinterpret that direct call as the recorder streaming snapshot API

### Requirement: Partial deltas SHALL be batched without crossing stream identities

After a stream anchor, the recorder SHALL retain later deltas in memory and flush them as one batch when the pending serialized event bytes reach 8 KiB or when 80 ms elapse on the current asyncio event loop. A new stream key SHALL flush pending deltas before its own anchor. Text, thinking and each tool call argument stream SHALL have independent keys derived from invocation, attempt, kind and tool call id; deltas from different identities SHALL never be concatenated.

#### Scenario: Small consecutive text deltas are coalesced

- **WHEN** a recorder receives multiple text deltas for one stream before either threshold is reached
- **THEN** only the first delta is synchronously persisted, later deltas remain buffered, and the next successful batch flush updates one snapshot with their concatenated text

#### Scenario: Byte threshold flushes one SQLite batch

- **WHEN** pending deltas reach or exceed 8 KiB of canonical serialized event bytes
- **THEN** the recorder flushes the pending events immediately using one downstream batch operation and no delta is written through a separate SQLite transaction

#### Scenario: A model-call invocation may differ from the run root invocation

- **WHEN** a Provider model-call event uses its request id as `invocation_id` while the enclosing run has a different root `runtime_run_state.invocation_id`
- **THEN** the partial batch is accepted after the model-call's own `invocation_opened` event is present, and the run is not rejected merely because the two invocation ids differ

#### Scenario: Timer threshold flushes pending deltas

- **WHEN** pending deltas remain below 8 KiB for 80 ms while the recorder is running on an asyncio event loop
- **THEN** the timer flushes the pending batch, and a later snapshot read contains the exact concatenated content

#### Scenario: A successful explicit retry clears a timer failure

- **WHEN** a timer flush fails, a subsequent explicit `flush_partials()` successfully persists the retained buffer, and the recorder receives another delta or retry
- **THEN** the recorder accepts the later operation without replaying the recovered timer error, and the rejected operation has not consumed a `partial_seq`; a stream-selective flush must retain the marker while another failed buffer remains pending

#### Scenario: Multiple tool calls stay isolated

- **WHEN** partial JSON fragments arrive for tool call A and tool call B in the same provider invocation
- **THEN** each call has its own snapshot and each snapshot contains only its own argument fragments in arrival order

### Requirement: Partial snapshot updates SHALL be idempotent and fail closed

The SQLite batch operation SHALL execute in one transaction, validate that all events are partial and belong to compatible session/run/invocation identities, and require a positive monotonic `partial_seq` from recorder-produced batch events. Replaying an already committed sequence SHALL not duplicate text or arguments. Identity conflicts, missing sequence metadata, malformed payloads, commit failures and closed-store failures SHALL be reported to the caller; the in-memory buffer SHALL not be discarded until the batch commit succeeds.

#### Scenario: Repeated batch delivery does not duplicate content

- **WHEN** the same partial batch is delivered again after a successful commit
- **THEN** the snapshot content, fragment count and digest remain unchanged and the batch reports an idempotent result

#### Scenario: A batch commit fails

- **WHEN** a fault is injected during partial batch commit
- **THEN** the transaction is rolled back, the prior durable snapshot remains readable, pending deltas remain available for explicit retry, and no success result is returned

#### Scenario: A stream identity conflicts

- **WHEN** a partial event reuses an existing stream key with a different session, run, invocation, attempt, kind or tool-call identity
- **THEN** the store raises a bounded validation/idempotency error and does not modify the existing snapshot

#### Scenario: A repeated sequence changes its payload

- **WHEN** an event reuses the latest committed `partial_seq` with a different event identity or event digest
- **THEN** the store raises an idempotency conflict and keeps the existing snapshot unchanged

#### Scenario: An optimized batch without sequence metadata fails closed

- **WHEN** `append_runtime_partial_batch()` receives a partial event without a positive `partial_seq`
- **THEN** the store rejects the batch before opening its SQLite transaction and does not create or modify a streaming partial snapshot; callers that need the legacy sequence-less behavior may continue using direct `append(partial_event)`

### Requirement: Final semantic events SHALL close their partial snapshots atomically

Before emitting a final text/thinking event, final tool-call event, provider error, budget terminal event or run finish, the recorder SHALL flush pending deltas. For SQLite, insertion of the final immutable event and deletion of the matching stream snapshots SHALL occur in one transaction. `retry` SHALL flush the prior attempt but SHALL NOT discard its durable partial snapshot until a terminal/final boundary closes the invocation.

#### Scenario: A final model event replaces an active text snapshot

- **WHEN** pending text deltas have been successfully flushed and `final_text` is emitted
- **THEN** the final event is present in `runtime_events` and the matching streaming partial snapshot is absent after the transaction commits

#### Scenario: Final tool call closes only its argument stream

- **WHEN** one invocation has pending argument snapshots for multiple tool calls and the recorder emits the final event for call A
- **THEN** call A's snapshot is deleted atomically with its final event while call B's snapshot remains until its own final or invocation terminal boundary

#### Scenario: Final transaction fails

- **WHEN** insertion of a final event or its snapshot cleanup fails
- **THEN** the transaction rolls back, the partial snapshot remains available, the recorder does not mark the model call finished, and the caller receives the canonical persistence failure

#### Scenario: Provider retry preserves prior partial evidence

- **WHEN** an invocation retries after receiving partial output
- **THEN** the old attempt's buffered content is flushed under its old stream key, the retry uses a new attempt stream key, and no old snapshot is silently overwritten by retry deltas

### Requirement: Cleanup and recovery SHALL not lose the last durable partial

Agent turn cleanup and `aclose()` SHALL flush recorder buffers before closing or flushing the canonical sink. Cancellation or process interruption MAY leave the last successfully committed partial snapshot, which SHALL be readable after reopening the store and SHALL be reported as bounded recovery evidence rather than fabricated model history or a tool outcome.

#### Scenario: Cancellation flushes before resource close

- **WHEN** an active Agent turn is cancelled after partial output but before a final provider event
- **THEN** cleanup attempts a partial flush before store close, and a successful flush is visible after reopening the SQLite database

#### Scenario: Reopened store exposes an unfinished stream

- **WHEN** a process stops after a partial snapshot commit and before a final event
- **THEN** a new store instance can read the snapshot by session/run/invocation, while model replay continues to use only immutable `runtime_events`

#### Scenario: Recovery sees pending partial evidence

- **WHEN** `RecoveryProjection` scans a run with a committed streaming partial and no terminal final event
- **THEN** it emits a bounded pending-partial diagnostic and does not treat the snapshot as a canonical assistant message, tool call fact or executable operation

### Requirement: Sinks without streaming extensions SHALL preserve ordered fallback boundaries

`RuntimeEventEmitter` SHALL detect optional streaming batch/final-cleanup methods. A sink that does not implement the streaming batch extension SHALL receive pending partial events through the existing `emit()` path in global arrival order. Before sending any ordinary lifecycle event or final/terminal event through that sink, the recorder SHALL flush all pending partials first. An extended SQLite sink SHALL retain per-stream batching and selective final cleanup; this fallback ordering rule SHALL NOT merge distinct stream identities.

#### Scenario: In-memory sink flushes pending partials before usage

- **WHEN** a recorder is constructed with a sink that implements only `emit`, `flush` and `close`, receives text partials `a` and `b`, and then receives a usage event
- **THEN** the sink observes `partial a`, `partial b`, and then `usage` in that order, the partials use the existing generic `emit()` path, and no optional-method error is raised

#### Scenario: In-memory sink flushes every stream before a final event

- **WHEN** a fallback sink has a pending tool-argument partial for call B and the recorder emits `final_text` for the text stream
- **THEN** the pending tool partial reaches the sink before `final_text`, while the partial payload remains associated with call B and is not concatenated with the text stream

#### Scenario: An extended sink returns no result after success

- **WHEN** a sink implements a streaming extension and returns `None` after accepting a batch or final cleanup
- **THEN** the emitter treats the extension as successful and does not resend the same event through the generic `emit()` path

#### Scenario: SQLite sink uses the optimized boundary

- **WHEN** a recorder is constructed with `RuntimeEventEmitter(SQLiteRuntimeStore(...))`
- **THEN** partial deltas use the snapshot/batch methods and final cleanup uses the atomic SQLite method without changing the final provider response or tool execution semantics
