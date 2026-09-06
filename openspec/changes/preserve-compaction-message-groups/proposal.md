## Why

Full compaction currently selects the last provider message as a preserved tail. When that message is a tool result, the summarizer receives a tool-use without its result and the post-compaction context receives the reverse. The same boundary error is possible on the OpenAI path and also discards original source identities needed by later lightweight transitions.

## What Changes

- Build compaction candidates from the canonical neutral projection rather than mutable provider arrays.
- Treat one model response and all of its tool results as an atomic message group for summary and tail selection.
- Never send a summarizer or provider request an orphaned tool call/result group.
- Preserve original source event IDs for retained messages; assign synthetic identity only to newly created summary messages.
- Make full-compaction reset replay retain source identity and remain compatible with a later lightweight compression transition.

## Capabilities

### New Capabilities

- `compaction-message-groups`: Defines group-safe compaction boundaries and source-preserving effective context reconstruction.

### Modified Capabilities

None.

## Impact

- Affects compaction input and checkpoint construction in `agent.py`, reset handling in both model projections, and compaction/replay tests.
- Changes the effective context only at an explicit full-compaction epoch transition.
- Provider-visible requests gain stronger tool-pairing guarantees; canonical raw facts remain append-only.
