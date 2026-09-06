## Purpose

Ensures every effective-context transition is proven applicable to the current canonical prefix before durable activation, keeping recovery fail-closed without leaving unusable checkpoints or transitions.

## ADDED Requirements

### Requirement: A transition is validated against its exact source

Before activation, the system SHALL verify source high-water, source digest, projection/policy versions, context identity/epoch, and every replacement target. A target event ID is authoritative; a tool-call ID MAY confirm identity but MUST NOT select an alternate target.

#### Scenario: Replacement target is missing

- **WHEN** a lightweight transition references an event not present in the active effective context
- **THEN** the transition is rejected before canonical append and the prior context remains active

#### Scenario: Duplicate call ID exists in another run

- **WHEN** a transition targets one response event while another run uses the same provider call ID
- **THEN** validation applies only to the exact event target and never modifies the other run's result

#### Scenario: Source changes during preparation

- **WHEN** new canonical events arrive after a candidate source high-water was read but before activation
- **THEN** the candidate is rejected or regenerated and no stale transition is committed

### Requirement: The proposed effective context is structurally valid

Validation SHALL apply the candidate to a copy of the active neutral context and verify provider message-group pairing, tool-call order, source identity, and final result digest before persistence.

#### Scenario: Full compaction candidate

- **WHEN** a full-compaction candidate contains summary and retained messages
- **THEN** validation confirms no orphaned tool call/result and confirms the digest of the final persisted representation

#### Scenario: Lightweight replacement candidate

- **WHEN** a replacement targets a retained tool result after a prior reset
- **THEN** validation locates the retained source event ID, checks the optional call ID, applies the replacement once, and verifies the resulting digest

### Requirement: Activation is atomic and fail-closed

Checkpoint persistence and its activation transition SHALL commit atomically where the authoritative store supports transactions. The new effective context MUST NOT be sent to a provider before activation succeeds; a failed activation SHALL preserve the last committed context and enter controlled run failure.

#### Scenario: SQLite transaction fails

- **WHEN** checkpoint or activation append fails inside the store transaction
- **THEN** neither half is visible as a committed activation and the prior context remains the request source

#### Scenario: Invalid candidate is submitted

- **WHEN** pre-commit validation fails
- **THEN** no unusable transition is appended and recovery continues to target the previous valid state

### Requirement: Recovery reports unverifiable transitions

Replay SHALL surface a diagnostic with transition identity and failure reason when a committed transition cannot be verified. It MUST NOT silently reinterpret the transition as a fresh uncompressed context or switch to a legacy source.

#### Scenario: Corrupt committed transition

- **WHEN** a stored transition has a mismatched digest or unsupported version
- **THEN** replay returns a controlled error/diagnostic and does not send a reconstructed provider request
