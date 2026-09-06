## Purpose

Provides a durable, provider-neutral tool operation boundary that records whether execution may have started and prevents ambiguous recovery from being treated as a safe retry.

## ADDED Requirements

### Requirement: Tool operations have stable identity

Each tool call MUST have an operation identity bound to the invocation, provider call identity, canonical argument hash, and recovery mode.

#### Scenario: Same call is retried by the provider

- **WHEN** a provider retry presents the same tool call identity and arguments
- **THEN** the runtime correlates it to the existing operation rather than creating an unrelated executable operation

#### Scenario: Arguments conflict

- **WHEN** an existing provider call identity is reused with a different canonical argument hash
- **THEN** the runtime rejects the operation and does not execute the tool

### Requirement: Dispatch is a durable execution boundary

The runtime MUST durably record dispatch before invoking a tool implementation or allowing its side effect to begin.

#### Scenario: Authorized tool execution

- **WHEN** permission is accepted and the tool is about to execute
- **THEN** a durable dispatch fact exists with operation identity and arguments hash before execution starts

#### Scenario: Dispatch persistence fails

- **WHEN** the dispatch fact cannot be durably stored
- **THEN** the tool is not invoked and the current run follows controlled failure handling

### Requirement: Unknown outcomes are fail-closed

An operation with durable dispatch but no durable outcome MUST be classified as outcome unknown and MUST NOT be automatically re-executed during recovery.

#### Scenario: Process interruption after dispatch

- **WHEN** recovery finds a dispatch without a matching outcome
- **THEN** recovery emits a diagnostic uncertain state and requires a new explicit invocation for any retry

### Requirement: Large results use verified artifact references

Tool results exceeding the bounded inline limit MUST be archived and referenced by content hash and size before they are projected into model context.

#### Scenario: Artifact archive succeeds

- **WHEN** a tool returns an oversized result and the artifact is durably archived
- **THEN** the canonical result contains a bounded reference and the replay projection does not inline the full payload

#### Scenario: Artifact archive fails

- **WHEN** an oversized result cannot be archived
- **THEN** the runtime records a controlled failure and does not fabricate a successful complete result
