## Purpose

将实际进入模型请求的记忆召回内容建模为独立、可审计、可重放的上下文事实，保证下一步请求和进程恢复不会悄然丢失或重复注入记忆。

## ADDED Requirements

### Requirement: Model-visible memory is a distinct durable context event

The system SHALL record memory injected into a model-visible request as a distinct context event containing the injected text, source identity, content digest, sequence/order information, and an idempotency key, and SHALL NOT mutate the original user-input event.

#### Scenario: Memory is injected after recall

- **WHEN** recall returns one or more eligible memories before a provider request
- **THEN** the system appends one durable context event representing the actual injected text and preserves the original user input unchanged

#### Scenario: Multiple memories have stable order

- **WHEN** recall returns multiple memories
- **THEN** the event records a deterministic order and Replay emits the same combined model context in that order

### Requirement: Memory injection is replayable and provider-neutral

The system SHALL project a committed memory context event into both Anthropic and OpenAI-compatible model histories without embedding provider-specific wire blocks in the canonical fact.

#### Scenario: Next request replays memory

- **WHEN** a memory context event has been committed and the Agent starts its next provider step
- **THEN** the next request contains the same memory text at the same logical context position

#### Scenario: Restart replays memory

- **WHEN** the process exits after committing a memory context event and a new process reopens the session
- **THEN** Model Replay and Session projections recover the memory event with its source and digest metadata

### Requirement: Memory consumption is commit-aware and idempotent

The system SHALL mark a recall result as consumed only after its Canonical context event has been durably accepted, and SHALL use the idempotency key to prevent duplicate injection on retry.

#### Scenario: Canonical write fails

- **WHEN** memory event persistence fails
- **THEN** the recall result remains retryable, the current request does not claim durable memory context, and the error is not swallowed as a successful consumption

#### Scenario: Consumer retries the same result

- **WHEN** the same recall result is presented again with the same idempotency key
- **THEN** the system reuses the existing event and does not append or inject a duplicate memory block

### Requirement: Memory context is observable without exposing unnecessary payloads

The system SHALL label memory injection in Session and Trace projections and SHALL expose source/digest/order diagnostics without requiring raw recall internals in unrelated events.

#### Scenario: Session distinguishes memory from user text

- **WHEN** a session projection contains a memory context event
- **THEN** the projected entry identifies it as context/memory rather than presenting it as an original user utterance

#### Scenario: Diagnostic is bounded

- **WHEN** a memory event is rejected or cannot be replayed
- **THEN** the diagnostic contains safe type/identity metadata and excludes unbounded or secret-bearing raw payloads
