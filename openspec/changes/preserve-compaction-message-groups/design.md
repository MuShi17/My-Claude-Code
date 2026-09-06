## Context

See `proposal.md` for motivation. `_compact_anthropic()` and `_compact_openai()` currently operate on mutable provider arrays and preserve only `messages[-1]`. `_compaction_context_messages()` then strips provider metadata and the reset branch in replay replaces every retained message's `runtime_event_id` with the compaction event ID.

## Goals / Non-Goals

**Goals:**

- Derive compaction candidates from one canonical neutral replay result.
- Define complete message groups for text, thinking, tool calls, and all tool results.
- Preserve retained event IDs through checkpoint/reset and support later exact-ID replacements.
- Make both provider summarizer requests structurally valid.

**Non-Goals:**

- Do not change summary wording or add a new summarization model.
- Do not remove raw canonical events after compaction.
- Do not solve arbitrary malformed historical ledgers beyond controlled diagnostics.

## Decisions

### 1. Compaction consumes neutral effective context

Before summarization, obtain the active context's `ModelReplayResult`. Build group units from its neutral messages, then adapt those units to Anthropic or OpenAI only for the summarizer request. This removes the current reverse conversion from a mutable provider array and gives compaction the same source identities as replay.

### 2. Group units contain calls and all results

An assistant tool-call message plus its complete ordered result set is one unit. Ordinary user/assistant text and thinking continuations are units at their natural message boundary. A unit with an unresolved call is pending and cannot be included in a provider request.

### 3. Checkpoint payload has explicit source identity

Retained neutral messages carry `runtime_event_id` (and tool call identity where relevant). The reset replay path honors an existing source ID and assigns the compaction event ID only when a synthetic message lacks one. Provider adapters strip these fields before sending.

### 4. Full compaction uses a staged candidate

The summarizer input and proposed effective context are computed before persistence. The checkpoint action contains the synthetic summary plus source-preserving retained units, a new epoch, and the source high-water/digest. The transition validator from the companion change verifies the candidate before atomic activation.

### 5. In-memory arrays are refreshed after activation

The provider arrays are not the source of truth. Once the reset event commits, Agent refreshes from canonical replay and adapter output. If activation fails, the previous arrays/epoch remain in effect and the error follows controlled runtime failure.

## Risks / Trade-offs

- [Risk] Very large neutral messages increase compaction candidate size → apply existing bounded materialization before checkpointing while computing digests over the final persisted values.
- [Risk] Historical malformed groups cannot be summarized → fail closed with a diagnostic rather than fabricating provider pairing.
- [Risk] Summary text has no original event identity → assign a deterministic synthetic identity derived from the activation event/checkpoint and keep it distinct from retained sources.

## Migration Plan

1. Add neutral group builder and source-preserving reset payload.
2. Adapt both summarizer requests from the group-safe neutral candidate.
3. Update cold/incremental reset handling and add chained-compaction tests.
4. Integrate pre-commit transition validation and run provider parity tests.

Rollback is to disable full compaction initiation; existing canonical facts remain readable.
