## Purpose

Makes the Agent Loop and its child runs emit lifecycle facts through one canonical runtime path while retaining ephemeral provider request materialization and privacy-safe capture as separate concerns.

## ADDED Requirements

### Requirement: Agent lifecycle uses canonical events only

The Agent and child-agent runtime MUST emit lifecycle facts through the canonical runtime path and MUST NOT require a legacy logger or tracer to complete a run.

#### Scenario: Normal provider turn

- **WHEN** an Agent performs a provider turn with text and tools
- **THEN** opening, model, tool, outcome, usage, and terminal facts are available in the canonical ledger without a legacy log writer

### Requirement: Provider context is ephemeral and canonical-derived

Provider-specific message buffers MUST be rebuilt from canonical replay at each request boundary and MUST NOT be used as the resume or lifecycle fact source.

#### Scenario: Message buffer is stale

- **WHEN** an in-memory provider message buffer is cleared or contains stale content before the next request
- **THEN** the next request is built from canonical replay and does not include the stale content

### Requirement: Canonical durability failures fail the run closed

If a required canonical append or terminal finalize fails, the active run MUST enter controlled failure handling and MUST NOT continue through a legacy writer.

#### Scenario: Terminal finalize fails

- **WHEN** the provider has returned but the terminal canonical event cannot be durably committed
- **THEN** the run reports controlled failure with the durability diagnostic and does not claim successful completion

### Requirement: Capture policy is explicit

LLM capture MUST honor off, metadata-only, and redacted modes, and off mode MUST NOT persist raw request or response bodies.

#### Scenario: Capture is off

- **WHEN** a provider request contains a synthetic secret marker and capture mode is off
- **THEN** no persisted capture or legacy log contains the raw request/response marker
