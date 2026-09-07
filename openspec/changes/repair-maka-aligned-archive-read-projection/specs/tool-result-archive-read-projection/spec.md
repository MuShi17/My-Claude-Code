## ADDED Requirements

### Requirement: Runtime SHALL expose a scoped ArchiveRead capability for archived tool results

The runtime SHALL expose an ArchiveRead tool only when the current Agent has a ToolResultArchiveCapability. The capability SHALL bind the reader and decoder to the current session and authorized Agent lineage, SHALL accept artifact refs rather than arbitrary file paths, and SHALL enforce bounded inspect, read, and query operations. The default read limit SHALL be 6000 units and the maximum read limit SHALL be 7500 units.

#### Scenario: ArchiveRead is advertised only with a capability

- **WHEN** an Agent builds a Provider request with an active archive capability
- **THEN** the request advertises exactly one ArchiveRead tool with operation, ref, offset, limit, and query inputs

#### Scenario: Agent without a capability cannot receive an archive placeholder that requires reading

- **WHEN** an Agent has no archive capability
- **THEN** the Provider tool set does not advertise ArchiveRead and a successful result SHALL NOT contain a ref whose only recovery path is ArchiveRead

#### Scenario: A bounded page can be read from a valid artifact ref

- **WHEN** ArchiveRead receives operation read, a valid artifact ref, offset 0, and a limit no greater than 7500
- **THEN** it returns a bounded page with artifact metadata, offset, next_offset, and has_more without returning an unbounded payload

#### Scenario: An invalid path-shaped input is rejected

- **WHEN** ArchiveRead receives a local file path instead of an artifact ref
- **THEN** it returns a structured parameter or not_found error and does not read the local path

### Requirement: ArchiveRead SHALL verify integrity and session or lineage scope

Before returning metadata or content, the archive reader SHALL validate the artifact ref, metadata, content hash and size, redaction metadata, and the current session or authorized lineage. It SHALL not use a global digest lookup as a substitute for scope authorization.

#### Scenario: A hash or size mismatch fails closed

- **WHEN** the artifact metadata or content does not match the requested ref hash or recorded size
- **THEN** ArchiveRead returns an integrity_mismatch error and does not return content as a successful page

#### Scenario: A ref from another session is denied

- **WHEN** an Agent requests an otherwise valid ref that is outside its session or lineage grant
- **THEN** ArchiveRead returns session_mismatch or scope_denied and does not disclose the other artifact metadata

#### Scenario: A closed archive store is not reopened implicitly

- **WHEN** ArchiveRead is called after the bound archive store has closed
- **THEN** it returns archive_store_closed and does not reopen or substitute another store

### Requirement: The first Provider request SHALL preserve a complete safe tool result when capacity permits

For a newly completed tool result that has not become stale, the Provider projection SHALL attempt to materialize the complete redacted artifact result at the first Provider request. If the complete result fits the effective request budget, the projected tool result SHALL contain the complete safe result rather than only bounded_ref metadata.

#### Scenario: A large read result fits the first request

- **WHEN** a large read_file result is archived and the complete redacted result fits the effective Anthropic request budget
- **THEN** the Anthropic tool result contains the complete safe file content and is not a bare bounded_ref object

#### Scenario: The OpenAI-compatible path preserves the same first-use behavior

- **WHEN** the same large result fits the effective OpenAI-compatible request budget
- **THEN** the OpenAI-compatible tool result contains the complete safe content with equivalent semantics

#### Scenario: Canonical facts remain unchanged by hydration

- **WHEN** Provider projection materializes a complete result from an archived ref
- **THEN** the canonical runtime event, artifact bytes, metadata, ref, and digest remain unchanged

### Requirement: Capacity rescue SHALL return a recoverable preview only when the first request cannot fit the full result

When a newly completed result cannot fit the effective first Provider request budget, the projection SHALL return a prefix preview of at most 4000 characters for text results, mark it truncated, preserve the artifact ref, and include the actual next offset and a callable ArchiveRead instruction. If the rescue envelope itself cannot fit, the projection SHALL retain at least the ref, truncated state, and ArchiveRead instruction.

#### Scenario: Capacity rescue returns a prefix preview

- **WHEN** a newly completed redacted tool result is larger than the remaining first-request budget
- **THEN** the Provider receives the first at most 4000 characters, truncated=true, ref, preview_chars, omitted information, next_offset, and an ArchiveRead read instruction

#### Scenario: The preview offset is the actual returned offset

- **WHEN** a text result is cut at a Unicode-safe boundary before the configured preview limit
- **THEN** next_offset equals the actual character count returned and a subsequent bounded ArchiveRead read can continue from that offset

#### Scenario: Capacity rescue is consistent across providers

- **WHEN** Anthropic and OpenAI-compatible requests enter the capacity rescue branch for the same artifact
- **THEN** both receive equivalent preview, ref, truncation, continuation, and ArchiveRead semantics after provider-specific wrapping

#### Scenario: The rescue envelope itself is too large

- **WHEN** the available request budget cannot contain the full preview envelope
- **THEN** the Provider receives a minimal actionable placeholder containing the ref, truncated state, and a usable ArchiveRead instruction

### Requirement: Stale archived results SHALL use an actionable placeholder and explicit read path

After a later completed step, Provider request, compaction transition, or equivalent context event makes a tool result stale, the Provider projection SHALL replace its old body with a bounded placeholder that includes the artifact ref and ArchiveRead instructions. It SHALL not silently re-inline the stale full result.

#### Scenario: A later model step makes a result stale

- **WHEN** a second completed step is present after a large archived tool result
- **THEN** the next Provider request contains a bounded placeholder with the ref and ArchiveRead instructions instead of the old full body

#### Scenario: The model reads a stale result explicitly

- **WHEN** the model calls ArchiveRead with a stale result ref and a bounded read limit
- **THEN** the next Provider context contains the requested bounded page as a normal read-only tool result

#### Scenario: A legacy placeholder lacks read instructions

- **WHEN** replay encounters a valid historical bounded_ref without read_instructions
- **THEN** the projection derives a safe default ArchiveRead instruction from the validated ref metadata without rewriting the historical artifact

### Requirement: Parent and child Agents SHALL share archive capability through scoped derivation

A child Agent SHALL receive a read-only derived archive capability when its parent grants one. The child capability SHALL preserve session or lineage scope and SHALL not be implemented by adding ArchiveRead to the ordinary child allowed-tools list. A child SHALL not close or replace a caller-owned archive or runtime store.

#### Scenario: A child reads an artifact within its grant

- **WHEN** a child Agent receives a parent-derived capability and requests a ref in its authorized lineage
- **THEN** the child can use ArchiveRead with the same bounded, redacted semantics as the parent

#### Scenario: A child cannot read outside its grant

- **WHEN** a child requests a valid ref from another session or ungranted lineage
- **THEN** ArchiveRead returns session_mismatch or scope_denied without content

#### Scenario: Ordinary child tools remain unchanged

- **WHEN** a child Agent is constructed with a normal allowed-tools configuration
- **THEN** ArchiveRead availability comes from the runtime capability and the ordinary allowlist does not gain unrelated tools or recursive Agent permission

#### Scenario: A child result remains readable by the parent

- **WHEN** a child returns an archived large result and the parent continues its own Provider request
- **THEN** the parent receives either safe content or an actionable placeholder with a ref it can read, not a second opaque bounded_ref layer

### Requirement: Terminal projection SHALL remain separate from Provider projection

The runtime SHALL maintain separate canonical, Provider, and terminal views of a tool result. Terminal output SHALL be bounded and human-actionable, SHALL use the same redaction and ref safety rules, and SHALL not implicitly hydrate an entire archive only because a ref is visible.

#### Scenario: A small result is displayed as content

- **WHEN** a small tool result is returned to the terminal
- **THEN** the terminal displays its safe content rather than archive metadata

#### Scenario: A large result is displayed with bounded guidance

- **WHEN** a large or stale result is returned to the terminal
- **THEN** the terminal displays a bounded preview or state, the ref, and a clear ArchiveRead guidance without dumping the full archive

#### Scenario: Provider content is not replaced by terminal status

- **WHEN** the terminal formats a bounded or stale result
- **THEN** the Provider projection remains independently generated and does not receive terminal status text as tool content

### Requirement: Archive errors and lifecycle failures SHALL not produce dangling successful refs

Archive write, metadata commit, read-back, decoder, and lifecycle failures SHALL remain fail-closed. A failure SHALL return a structured bounded error or a still-safe inline result when available, but SHALL not publish a successful artifact ref that cannot be read through the current capability. ArchiveRead and Provider projection SHALL not leak stack traces, archive root paths, secrets, or other session content.

#### Scenario: Metadata commit fails after an oversized tool result

- **WHEN** the runtime store rejects the archive metadata commit
- **THEN** the tool outcome contains an archive error without a successful ref and the canonical event is not rewritten as a false success

#### Scenario: Decoder is unavailable for a placeholder

- **WHEN** a result would require ArchiveRead but the bound capability has no usable decoder
- **THEN** the runtime fails in a controlled way or keeps a safe inline result if it fits, and does not send an unreadable placeholder as a successful result

#### Scenario: Provider and reader tasks finish before close

- **WHEN** an Agent or child completes a turn while a Provider projection or ArchiveRead operation is pending
- **THEN** those operations settle before an Agent-owned archive store is closed, and a caller-owned store remains caller-managed

#### Scenario: Error details are bounded and safe

- **WHEN** ArchiveRead fails due to missing ref, bad range, scope, integrity, or lifecycle state
- **THEN** the Provider and terminal receive a stable error code and bounded message without local archive paths, traceback, or unrelated metadata

