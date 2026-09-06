## Why

Anthropic-compatible providers can return response blocks whose text-bearing fields are not runtime strings. The current Agent path forwards those values directly into Canonical RuntimeEvent creation, so a valid multi-turn execution can fail locally with `content.text: text content requires a string` after the provider request itself has succeeded. This change closes the provider-to-canonical type boundary and makes malformed content diagnosable and controlled.

## What Changes

- Add a provider-response normalization boundary for Anthropic and OpenAI model output before Canonical RuntimeEvent recording.
- Accept only string text/thinking payloads for canonical text-bearing events, with explicit handling for valid empty signed thinking.
- Record bounded diagnostics that identify the provider, block kind, block index, and rejected value type without leaking raw payloads.
- Prevent malformed provider content from producing invalid Canonical RuntimeEvents or falsely successful terminal runs.
- Add regression coverage for malformed text/thinking blocks, signed thinking, normal multi-tool responses, and canonical event invariants.

## Capabilities

### New Capabilities

- `canonical-provider-response-normalization`: Normalize and validate provider response content before it enters the canonical event ledger.

### Modified Capabilities

None.

## Impact

Affected code includes `agent.py`, `runtime_lifecycle.py`, the Canonical RuntimeEvent boundary, provider replay fixtures, and runtime/acceptance tests. No provider API dependency or legacy-log compatibility behavior is added; existing thinking/signature and tool-replay changes remain in scope as prerequisites for the regression suite.
