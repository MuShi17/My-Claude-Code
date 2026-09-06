## Purpose

为上下文压缩建立持久化、可验证、可重放的有效上下文变更，使压缩后的模型历史不会在下一次 Replay 中被原始事件隐式恢复。

## ADDED Requirements

### Requirement: Context changes are durable and explicitly versioned

The system SHALL represent every model-history reduction that affects a future provider request as a durable transition with a source high-water, source digest, projection version, policy version, context epoch, reason, and deterministic result digest.

#### Scenario: Lightweight reduction is replayable

- **WHEN** a tool result is budget-truncated, snipped, or microcompacted
- **THEN** the effective replacement and its target identity are durably recorded before a later request uses the replacement

#### Scenario: Effective context has an epoch

- **WHEN** a full compaction or context reset replaces an earlier history prefix
- **THEN** the new effective context belongs to a new context epoch that is visible in diagnostics and replay metadata

### Requirement: Checkpoint activation is atomic

The system SHALL commit the checkpoint and the transition that activates it as one durable transaction, or commit neither, and SHALL not send a newly reduced context after a failed activation.

#### Scenario: Activation succeeds

- **WHEN** checkpoint and transition persistence complete successfully
- **THEN** subsequent replay applies the committed effective context and exposes the same source coverage and epoch

#### Scenario: Activation fails during commit

- **WHEN** persistence fails before the checkpoint/transition transaction commits
- **THEN** the previous effective context remains authoritative and the current run enters controlled failure handling before sending the new context

### Requirement: Replay applies committed context changes deterministically

The system SHALL apply committed context transitions in ordinal order and SHALL not re-decide historical reductions from current time, token utilization, or current policy thresholds.

#### Scenario: Restart preserves a reduced result

- **WHEN** the process restarts after a lightweight or full context transition was committed
- **THEN** replay reconstructs the same effective message representation without restoring the removed original content

#### Scenario: Transition source is invalid

- **WHEN** source high-water, source digest, coverage, or transition result digest cannot be verified
- **THEN** the system preserves the Canonical Store, reports a bounded diagnostic, and fails closed rather than activating the unverifiable context

### Requirement: Tool message groups remain valid after reduction

The system SHALL reduce tool history at complete call/result group boundaries and SHALL preserve the association between every retained tool call and its corresponding tool result.

#### Scenario: Multiple tools are reduced together

- **WHEN** a model response contains multiple tool calls and the effective context is reduced
- **THEN** retained calls and results remain in one provider-valid group with no dangling call or result

#### Scenario: Recent tail includes tool history

- **WHEN** a checkpoint retains a recent tail containing tool activity
- **THEN** the tail preserves the required call/result pairing and provider-visible ordering
