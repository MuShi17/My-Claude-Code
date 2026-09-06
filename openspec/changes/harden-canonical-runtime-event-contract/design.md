## Context

The repository already has a Canonical RuntimeEvent and SQLite append store, but the envelope still contains legacy normalization and the store primarily exposes a global ordinal. Maka's runtime contract uses a complete opening fact, strict storage-owned order, immutable ledger semantics, bounded partial snapshots, and a terminal durability barrier.

## Goals / Non-Goals

**Goals:**

- Make canonical events self-describing and sufficient for replay/recovery boundaries.
- Preserve the existing session ordinal while adding invocation-local ordering.
- Make validation and terminal sealing deterministic.

**Non-Goals:**

- Import Maka's Agent Graph, workspace authority, distributed continuation, or provider-specific wire protocol wholesale.
- Guarantee byte-identical provider HTTP requests.

## Decisions

1. Store both `event_seq` and the existing session ordinal. `event_seq` is the replay and invocation integrity boundary; the session ordinal remains useful for cross-run projections.
2. Require one rich opening event per invocation. Provider/model and execution configuration are immutable opening data, not later metadata assembled by a projection.
3. Keep partial output in bounded mutable snapshots. Partial events are never model-visible history.
4. Make exact duplicate event IDs idempotent and reject same-ID payload conflicts, sequence gaps, terminal-after-seal appends, and terminal events that are not ledger tails.
5. Bump the canonical schema version and remove input normalization rather than preserving ambiguous legacy defaults.

## Risks / Trade-offs

- [Schema break] Existing fixtures and future callers may use old payloads → update all canonical fixtures and fail clearly on legacy shapes.
- [Migration ordering] Tool/replay changes may depend on new refs → land this change before tool journal and compatibility removal.
- [Extra index] Two order dimensions add storage/index cost → index by invocation sequence and session ordinal only where projections query them.

## Migration Plan

Implement and validate this contract while the first-batch compatibility code still exists. Do not convert old files. The later cleanup change removes their readers after all canonical fixtures and projections use the strict envelope.
