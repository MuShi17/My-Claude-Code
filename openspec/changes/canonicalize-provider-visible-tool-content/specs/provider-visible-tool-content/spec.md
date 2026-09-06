## Purpose

为 Agent 建立稳定且可重建的模型可见工具结果表示，使首次请求、Canonical Replay、压缩上下文和 Provider wire payload 在同一历史边界内保持一致。

## ADDED Requirements

### Requirement: Tool results have one deterministic model-visible representation

The system SHALL classify each completed tool result as text, a deterministic JSON value, or an explicitly validated content-block sequence, and SHALL use the resulting representation consistently for the initial provider request and all later replays.

#### Scenario: Plain text result remains text

- **WHEN** a tool returns a string result within the configured bound
- **THEN** the model-visible result contains the same text without JSON object wrapping or lossy coercion

#### Scenario: Structured result uses deterministic JSON

- **WHEN** a tool returns a mapping or sequence that is not an explicitly valid content-block sequence
- **THEN** the model-visible result uses one deterministic JSON encoding whose key ordering, Unicode handling, separators, and numeric validity are stable across process restart

#### Scenario: Bounded artifact result survives replay

- **WHEN** a large result is replaced by a bounded artifact placeholder and the Canonical Store is closed and reopened
- **THEN** the first materialized placeholder and the replayed placeholder have identical model-visible serialized bytes

### Requirement: Provider adapters emit valid tool-result messages

The system SHALL convert the neutral model-visible tool result into the target Provider message shape and SHALL NOT pass an arbitrary mapping as a Provider field that accepts only a string or a validated content-block sequence.

#### Scenario: Anthropic tool result is wire-valid

- **WHEN** an Anthropic request contains a structured or bounded tool result
- **THEN** `tool_result.content` is a string or an explicitly validated list of supported content blocks

#### Scenario: Invalid block-like list is not treated as multimodal content

- **WHEN** a tool returns a list that does not satisfy the supported content-block schema
- **THEN** the adapter serializes it as deterministic JSON or returns a bounded controlled diagnostic, and does not send an invalid block list

### Requirement: Shared prefix materialization is byte-stable

The system SHALL preserve byte equality for the shared history prefix when the same Canonical events are materialized for an initial request, a subsequent tool-result request, and a request rebuilt after SQLite reload.

#### Scenario: Replay preserves the request prefix

- **WHEN** a request with a tool result is captured, the resulting events are appended, and the next request is built from Replay
- **THEN** the bytes for the shared messages before newly appended content are identical to the captured prior request prefix

#### Scenario: Nested and Unicode values remain stable

- **WHEN** a tool result contains nested mappings, arrays, non-ASCII text, and bounded metadata
- **THEN** repeated materialization produces identical serialized bytes and does not introduce provider-invalid content types

### Requirement: Existing privacy and failure boundaries remain enforced

The system SHALL apply redaction and bounding before deterministic materialization and SHALL preserve controlled failure behavior when a provider-visible value cannot be represented safely.

#### Scenario: Secret does not enter a materialized result

- **WHEN** a tool result contains a configured secret marker and is materialized for the model
- **THEN** the marker is absent from the Provider payload and from any diagnostic emitted for the normalization failure

#### Scenario: Unsupported provider content fails closed

- **WHEN** a response or tool result cannot be normalized into a valid model-visible representation
- **THEN** the current Run enters the existing controlled failure path before successful terminal finalization
