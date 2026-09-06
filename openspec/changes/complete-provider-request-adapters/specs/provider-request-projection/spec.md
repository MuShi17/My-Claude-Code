## Purpose

Ensures every model request reconstructed from canonical history conforms to the selected provider protocol while remaining deterministic across first-use, warm replay, cold replay, and recovery.

## ADDED Requirements

### Requirement: OpenAI tool calls use the provider wire contract

For OpenAI-compatible requests, every assistant tool call SHALL be encoded as an object with `id`, `type: "function"`, and `function.name` plus a JSON-string `function.arguments`. Neutral fields such as runtime event IDs MUST NOT appear in the request body.

#### Scenario: Replaying one tool call

- **WHEN** canonical history contains an assistant function call with structured arguments
- **THEN** the captured OpenAI request contains the provider `tool_calls` shape and `function.arguments` is a deterministic JSON string

#### Scenario: Replaying multiple tool calls

- **WHEN** one assistant response contains multiple calls
- **THEN** all calls are emitted in one assistant message in model order with distinct provider IDs and valid function objects

### Requirement: Tool results are provider-valid and deterministic

The adapter SHALL convert non-string tool results to deterministic JSON text or a provider-supported content-block representation. The same neutral value SHALL produce byte-equivalent outbound content in first-use, warm replay, cold replay, and reopened-store recovery.

#### Scenario: Structured tool result

- **WHEN** a tool result is a nested mapping or sequence
- **THEN** the outbound provider message contains valid content and repeated projection paths produce the same serialized bytes

#### Scenario: Bounded artifact result

- **WHEN** a large result is represented by a bounded artifact placeholder
- **THEN** the placeholder is sent consistently and the raw archived result is not reintroduced by replay

### Requirement: Provider-specific thinking and message pairing are preserved

The adapter SHALL preserve provider-valid thinking/signature state where required and SHALL place each tool result after its corresponding assistant tool-use message. It MUST NOT expose a neutral `kind` block or unsigned foreign-provider reasoning block to a provider that cannot validate it.

#### Scenario: Anthropic signed thinking followed by tools

- **WHEN** canonical history contains signed Anthropic thinking and one or more tool calls
- **THEN** the request retains the thinking signature and emits one assistant content array followed immediately by the matching user tool-result blocks

#### Scenario: OpenAI reasoning history

- **WHEN** canonical history contains OpenAI reasoning content
- **THEN** the adapter places it in the configured OpenAI reasoning field and does not emit it as an Anthropic signed block

### Requirement: Cold and warm provider requests are equivalent

For the same canonical high-water, context, and epoch, the provider adapter SHALL produce equivalent ordered request messages regardless of whether the neutral projection came from an incremental cursor or a cold rebuild.

#### Scenario: Warm versus cold capture

- **WHEN** a request is captured from both incremental replay and a fresh SQLite projection
- **THEN** the provider request bodies match after excluding transport headers and runtime-only diagnostics
