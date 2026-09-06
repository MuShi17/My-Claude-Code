## Context

See `proposal.md` for motivation. `ModelReplayProjection` deliberately uses neutral `{id, name, arguments}` tool calls, while `_openai_messages()` currently copies the neutral message and only adapts tool results. Anthropic conversion already has special handling for signed thinking but must remain isolated from OpenAI-specific fields.

## Goals / Non-Goals

**Goals:**

- Make `CanonicalModelContextAdapter` the only boundary that creates provider wire messages.
- Produce deterministic OpenAI function-call arguments and valid tool messages.
- Preserve source metadata only inside neutral/canonical state and never in outbound payloads.
- Exercise actual SDK serialization through a local mock transport.

**Non-Goals:**

- Do not change provider response parsing or model selection.
- Do not normalize provider-specific reasoning into one lossy universal format.
- Do not promise identical JSON key ordering if a provider SDK reserializes the final body; compare the adapter payload before transport and the captured body where the SDK preserves it.

## Decisions

### 1. Keep one neutral-to-wire adapter boundary

Refactor `_openai_messages()` into explicit message constructors. Assistant calls are rebuilt as `{id, type, function}` objects; tool messages are rebuilt with `role`, `tool_call_id`, and materialized content. This is safer than allowing a neutral dict to pass through because internal fields and argument types cannot leak accidentally.

### 2. Use canonical JSON for function arguments

Arguments are normalized through the existing JSON-value conversion and encoded with sorted keys, UTF-8, compact separators, and `ensure_ascii=False`. Invalid raw argument strings remain strings only when they cannot be parsed, preserving evidence without producing a mapping where a JSON string is required.

### 3. Centralize tool-result materialization

Reuse `materialize_tool_result()` for both providers, with explicit provider rules. Anthropic can use string or content-block arrays; OpenAI tool messages use a provider-supported string/parts shape, and arbitrary mappings are never passed as `content` objects.

### 4. Test the actual request boundary

Add a mock HTTP transport around the installed SDK where practical, plus direct adapter assertions. Tests compare first-use, incremental, cold, and reopened-store request bodies and assert absence of `runtime_event_id`, `context_type`, and neutral tool-call fields.

## Risks / Trade-offs

- [Risk] Some OpenAI-compatible gateways accept extensions not accepted by the official schema → target the strict Chat Completions shape and keep gateway-specific fields out of neutral replay.
- [Risk] An invalid legacy argument string cannot be parsed → preserve it as a JSON string value and expose a diagnostic rather than sending a dict.
- [Risk] SDKs may add transport fields → assertions separate provider message semantics from headers/telemetry.

## Migration Plan

1. Add adapter helpers and direct wire-shape tests.
2. Route Agent OpenAI requests and recovery through the adapter.
3. Add Anthropic parity assertions for thinking signatures and tool grouping.
4. Run the complete regression suite.

Rollback is limited to selecting the previous adapter implementation; canonical events remain unchanged.
