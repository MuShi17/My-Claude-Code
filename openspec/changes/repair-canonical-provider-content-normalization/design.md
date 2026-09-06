## Context

The current Anthropic-compatible Agent path receives SDK response objects, streams their display fields, and later forwards `block.text` or `block.thinking` directly to `ModelCallRecorder`. `RuntimeEvent` correctly validates both `text` and `thinking` content through the shared `content.text` field, but the caller currently assumes the provider SDK has already normalized every compatible-provider response. The latest DeepSeek run disproves that assumption: provider requests and thinking replay succeeded, then canonical recording failed with `content.text: text content requires a string`. A second failure mode is the canonical redaction layer converting a long, otherwise valid model string into a bounded-reference mapping before persistence; that mapping is also invalid for `content.text` and cannot be replayed as signed thinking.

Maka places this boundary in its model adapter: provider chunks are type-checked, only string text deltas become normalized thinking/text events, and provider metadata such as Anthropic signatures is carried separately into step accumulation and replay.

## Goals / Non-Goals

**Goals:**

- Make every provider-to-canonical text-bearing value type-safe before event creation.
- Preserve valid Anthropic thinking signatures and OpenAI-compatible reasoning
  content, together with existing tool-call ordering.
- Make malformed compatible-provider responses observable without logging raw response payloads.
- Ensure Canonical authority cannot finish the affected run successfully after rejected content.

**Non-Goals:**

- Do not change the provider request envelope or tool-result wire serialization already fixed by earlier changes.
- Do not coerce arbitrary dictionaries or lists with `str()` and pretend they are model text.
- Do not add a general-purpose provider SDK abstraction or copy Maka's full step engine.
- Do not introduce legacy-log compatibility for malformed responses.

## Decisions

1. **Normalize at the Agent/provider response boundary.** Add one small, provider-neutral helper used by both Anthropic response recording branches for text and thinking. It accepts a string unchanged, permits the explicitly valid empty signed-thinking case, and returns a structured rejection for other types. This is preferable to weakening `RuntimeEvent` validation because the canonical schema must remain strict.

2. **Reject rather than stringify unknown values.** Converting a mapping or list to `str()` would create Python-specific, non-replayable content and could expose sensitive fields. Unknown shapes become a controlled canonical failure with bounded metadata. Any future provider-specific wrapper must be handled by an explicit adapter rule and test.

3. **Keep signature handling orthogonal to text normalization.** A valid signature is retained only as a string on a thinking event. The thinking text is still required to be a string; a signature cannot justify storing the raw provider object. This preserves the earlier DeepSeek/Anthropic replay repair.

4. **Treat OpenAI-compatible reasoning as provider-marked replay state.** Maka
   records unsigned reasoning as neutral thinking plus provider options, then
   maps it back to the route-specific `reasoning_content` field. The runtime
   follows the same narrow rule: only thinking produced by the OpenAI path is
   re-emitted to OpenAI, and it is assembled with adjacent visible text and
   tool calls as one assistant step. Anthropic signed thinking remains the
   only thinking block emitted by the Anthropic adapter.

5. **Diagnose with type metadata only.** The rejected block diagnostic includes provider, block kind, zero-based response index, and a stable value type label. It does not include `repr(value)`, serialized payloads, or response excerpts. The diagnostic uses the existing runtime error/controlled-failure path so terminal success is impossible.

6. **Test through both value-object and Agent integration levels.** Unit tests cover the normalizer and recorder boundary, including falsey non-string OpenAI stream deltas. Integration tests construct representative Anthropic-compatible responses containing malformed text/thinking, signed thinking, and multiple tool calls, plus an OpenAI-compatible reasoning/tool-call step, then assert canonical events, replay shape, and failure status.

## Risks / Trade-offs

- [Provider shape variation] A provider may return a semantically recoverable wrapper that is not a string → reject it first and add a narrowly scoped explicit adapter rule only after capturing a safe type/shape fixture.
- [Canonical text size] Generic redaction must not replace replay-critical model text/thinking with an object → preserve the string after secret scanning, while continuing to bound tool results and other arbitrary payloads.
- [Loss of malformed content] Rejected content is not persisted as model text → preserve only bounded diagnostic metadata and rely on provider/benchmark logs for the controlled failure; this protects canonical integrity and secrets.
- [Failure timing] The run fails after a successful provider response but before tool execution → this is intentional for Canonical authority; executing tools from an incompletely recorded model response would make replay and recovery ambiguous.
- [Existing dirty changes] The worktree contains the previous thinking/signature repair → implementation must preserve and test those changes rather than resetting or rewriting unrelated paths.

## Migration Plan

Implement the helper and diagnostics in the current canonical path, add focused regression tests, then run the py313 test suite and OpenSpec validation. Re-run the vulnerable-secret smoke with the same DeepSeek configuration. If the provider still returns an unrecognized shape, retain Canonical controlled failure and extend the explicit adapter fixture; do not bypass validation or fall back to legacy authority.
