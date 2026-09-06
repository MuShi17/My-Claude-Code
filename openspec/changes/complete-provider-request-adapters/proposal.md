## Why

Canonical replay currently produces a provider-neutral tool-call shape, but the OpenAI adapter can pass that internal shape directly to the SDK. The next request after tool execution and recovery requests therefore contain invalid wire fields even when cold and warm neutral projections compare equal.

## What Changes

- Convert neutral assistant tool calls to the exact OpenAI Chat Completions request shape.
- Encode function arguments as deterministic JSON strings and materialize tool results as provider-valid content.
- Strip runtime event and projection metadata at the provider boundary while preserving internal source identities in canonical state.
- Keep Anthropic tool-use/result grouping and thinking signatures valid through the same adapter boundary.
- Add SDK/mock-transport request-body tests for first use, warm replay, cold replay, recovery, multi-tool calls, and thinking.

## Capabilities

### New Capabilities

- `provider-request-projection`: Defines valid, deterministic provider request messages produced from canonical neutral history.

### Modified Capabilities

None.

## Impact

- Affects `src/mini_claude/projections/provider_context.py`, provider request construction in `agent.py`, replay integration, and provider-focused tests.
- Changes only the outbound wire representation; canonical event and neutral projection identities remain internal and unchanged.
- Covers both OpenAI-compatible and Anthropic adapters without adding a provider SDK dependency.
