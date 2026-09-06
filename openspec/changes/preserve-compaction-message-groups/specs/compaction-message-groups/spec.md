## Purpose

Defines safe compaction boundaries that preserve complete provider message groups, source identity, and replay equivalence across full and lightweight context reduction.

## ADDED Requirements

### Requirement: Compaction boundaries are message-group atomic

Full compaction SHALL select summary input and retained tail only at boundaries between complete user/model/tool message groups. A tool call and its results MUST NOT be separated in either the summarizer request or the activated context.

#### Scenario: Last canonical event is a tool result

- **WHEN** full compaction is requested immediately after tool execution
- **THEN** the summarizer receives the complete assistant tool-use and matching tool-result group, and the retained tail does not contain an orphan

#### Scenario: Multiple parallel tools finish out of order

- **WHEN** one assistant response invokes multiple tools and results complete in reverse order
- **THEN** compaction still emits one complete group with calls in model order and all matching results

#### Scenario: An incomplete tool group exists

- **WHEN** compaction observes a call without a valid durable result
- **THEN** that incomplete group is not sent as a provider or summarizer message and the run records a controlled diagnostic/failure according to the runtime policy

### Requirement: Retained context preserves source identity

Compaction checkpoints SHALL preserve the original source event identity for every retained canonical message. Synthetic summary/acknowledgement messages SHALL have a distinct compaction-event identity and MUST NOT overwrite retained source identities.

#### Scenario: Full compaction followed by lightweight compression

- **WHEN** a retained tool result is reduced after a full compaction reset
- **THEN** the lightweight transition can address the retained result's original response event ID and replay succeeds

#### Scenario: Reopen after compaction

- **WHEN** the store is closed and reopened after a full compaction
- **THEN** cold replay reconstructs the same effective context and source mappings as the warm path

### Requirement: Full compaction changes context atomically

The activated compacted context SHALL be associated with a new context epoch and SHALL become visible to the next provider request only after its checkpoint and reset transition are durably committed.

#### Scenario: Checkpoint activation succeeds

- **WHEN** a complete group-safe checkpoint and reset transition commit
- **THEN** the next request uses the new epoch and contains no pre-reset orphaned tool messages

#### Scenario: Checkpoint activation fails

- **WHEN** checkpoint or transition persistence fails
- **THEN** the prior committed effective context remains active and no uncommitted compacted candidate is sent

### Requirement: Cold and incremental compaction replay agree

For the same canonical high-water and context epoch, cold and incremental replay SHALL produce equal ordered neutral messages, source identities, tool pairings, and provider-adapted requests after compaction.

#### Scenario: Warm/cold comparison after reset

- **WHEN** a cursor processes the reset event incrementally and a fresh projection reads the complete ledger
- **THEN** both results have equal context epoch, message structure, source IDs, and provider wire messages
