## Purpose

This capability defines the strict boundary between provider responses and the canonical runtime ledger, ensuring that text-bearing model content is replay-safe, type-valid, and diagnosable when a compatible provider returns an unexpected shape.

## ADDED Requirements

### Requirement: Canonical text-bearing content is normalized before persistence
The runtime SHALL normalize provider response blocks before creating canonical model events. A canonical `text` or `thinking` event MUST contain a string in `content.text`; provider-specific raw objects, arrays, nulls, and SDK wrapper values MUST NOT cross the canonical event boundary unchanged.

#### Scenario: Normal text block is persisted
- **WHEN** a provider returns a text block whose text value is a string
- **THEN** the runtime records one canonical text event with that exact string

#### Scenario: Normal signed thinking block is persisted
- **WHEN** a provider returns a thinking block whose thinking value and signature are strings
- **THEN** the runtime records one canonical thinking event with the thinking string and preserves the signature for replay

#### Scenario: Valid empty signed thinking is preserved
- **WHEN** a provider returns a signed thinking block with an empty string as its thinking value
- **THEN** the runtime records the empty string and its signature without manufacturing a non-string placeholder

### Requirement: Malformed provider content is rejected in a controlled and diagnosable way
If a provider text-bearing value cannot be normalized to a string, the runtime MUST prevent creation of an invalid canonical event and MUST produce a controlled failure or equivalent rejected-response outcome. The diagnostic MUST identify the provider, block kind, block index, and rejected value type, while excluding the raw content value and secrets.

#### Scenario: Non-string thinking is returned
- **WHEN** a thinking block contains a mapping, list, null, or another non-string value
- **THEN** no invalid thinking event is appended, a bounded type-only diagnostic is emitted, and the current canonical run cannot be reported as successful

#### Scenario: Non-string text is returned
- **WHEN** a text block contains a mapping, list, null, or another non-string value
- **THEN** no invalid text event is appended, a bounded type-only diagnostic is emitted, and the current canonical run cannot be reported as successful

#### Scenario: Rejected content does not leak provider payloads
- **WHEN** normalization rejects a provider block that contains sensitive or large content
- **THEN** diagnostics contain only safe metadata such as provider, block kind, index, and value type, not the raw value or an unbounded serialization

### Requirement: Provider replay semantics remain intact after normalization
Normalization MUST preserve provider replay metadata that is valid for the selected adapter, including Anthropic thinking signatures, and MUST NOT alter the ordering or identity of model text, thinking, and tool-call blocks that are otherwise valid.

#### Scenario: Multi-tool response remains replayable
- **WHEN** one valid provider response contains thinking, text, and multiple tool calls
- **THEN** canonical recording preserves all valid blocks and the next provider context retains one assistant step with its matching tool calls

#### Scenario: OpenAI-compatible reasoning remains replayable
- **WHEN** an OpenAI-compatible response contains string `reasoning_content` and tool calls
- **THEN** canonical recording stores the reasoning as a valid thinking fact and the OpenAI adapter replays it as `reasoning_content` on the same assistant message as its visible text and matching tool calls

#### Scenario: Invalid block does not create a replay orphan
- **WHEN** a response contains an invalid text-bearing block alongside tool calls
- **THEN** the runtime fails the response before producing a replay history with a dangling or semantically invented model block
