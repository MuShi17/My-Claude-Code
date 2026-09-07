## ADDED Requirements

### Requirement: Session resources outlive individual turns

Agent MUST treat the Runtime Store, Runtime Event Emitter, Artifact Archive, and LLM capture manager as session-level resources. When Agent creates a Runtime Store, consecutive `chat()` calls MUST reuse the same live Store and its Archive binding; ending one chat turn MUST NOT close or discard those session resources.

#### Scenario: Consecutive turns archive a large tool result

- **WHEN** an Agent without a caller-owned Store completes one chat and then executes a tool result larger than the durable inline limit in a second chat
- **THEN** the second turn uses a live Runtime Store, returns a valid bounded artifact reference, and persists matching artifact metadata without a `runtime store is closed` error

#### Scenario: Turn finalization does not close the session Store

- **WHEN** a chat turn reaches normal, failed, cancelled, or budget terminal finalization
- **THEN** the turn state is finalized and flushed, but an Agent-owned Runtime Store remains available for the next turn until Agent close

### Requirement: Archive and canonical sink identities remain consistent

The Runtime Facade MUST keep the Runtime Event Emitter sink, the current canonical Store/Sink, and any automatically-created Artifact Archive metadata store bound to the same live resource identity. If an active facade detects a changed or incompatible resource identity, it MUST fail before emitting new runtime facts or executing a tool rather than silently using a stale dependency.

#### Scenario: Automatic Archive follows the current Store

- **WHEN** the Runtime Facade is first initialized without a caller-owned Archive
- **THEN** the automatically-created Archive metadata store is the same Runtime Store object used by the Runtime Event Emitter

#### Scenario: Stale resource identity is rejected

- **WHEN** an active Agent's canonical Store or Sink is replaced or its automatic Archive no longer points to that resource
- **THEN** the next facade setup reports a controlled resource-mismatch failure and does not execute the tool against the stale binding

### Requirement: Resource ownership is explicit

Agent MUST close only resources that it created. A caller-owned Runtime Store, Event Sink, or Artifact Archive MUST remain caller-managed, including when passed to a parent or sub-agent. Agent close MUST be safe to call more than once.

#### Scenario: Caller-owned Store survives Agent close

- **WHEN** a caller passes an open Runtime Store to an Agent and invokes Agent close
- **THEN** the Agent flushes its use of the Store but does not close it, and the caller can still read from or close the Store

#### Scenario: Shared sub-agent Store is not closed by the child

- **WHEN** a parent passes its Runtime Store and Archive to a sub-agent and the sub-agent completes a turn
- **THEN** the sub-agent does not close or replace the shared resources, and the parent can append another turn

#### Scenario: Agent close is idempotent and terminal

- **WHEN** Agent close is invoked multiple times and then a new chat is attempted
- **THEN** repeated close calls do not duplicate failures, and the new chat is rejected with a controlled closed-Agent error

### Requirement: CLI owns process-level cleanup

The REPL and one-shot CLI paths MUST call Agent close in an outer cleanup boundary for normal exit, EOF, interruption, and failure. A Store opened by CLI for resume MUST be closed by the CLI owner after Agent cleanup and MUST NOT be implicitly closed by Agent.

#### Scenario: REPL exits after multiple turns

- **WHEN** the user exits the REPL after one or more chats
- **THEN** Agent flushes/saves and closes its owned resources exactly once before the process returns

#### Scenario: One-shot failure still releases resources

- **WHEN** a one-shot chat raises a provider or canonical error
- **THEN** the CLI runs Agent cleanup before returning the error status

### Requirement: Archive failure does not create a dangling reference

The durable tool boundary MUST continue to archive an oversized result before publishing its bounded reference. If any archive file, metadata, or Runtime Store metadata commit fails, the outcome MUST contain no artifact reference that cannot be read and verified, and existing canonical event history MUST NOT be rewritten.

#### Scenario: Closed metadata Store rejects an oversized result

- **WHEN** an oversized tool result reaches an Archive whose metadata Store is closed
- **THEN** the tool outcome is a bounded archive error without a `ref`, and no false successful bounded reference is emitted

#### Scenario: Existing content is mirrored after a clean reopen

- **WHEN** a complete content-addressed artifact exists and a newly opened live Store archives the same content
- **THEN** the Archive verifies the existing content and can commit the missing metadata mirror without changing the artifact reference or canonical event history
