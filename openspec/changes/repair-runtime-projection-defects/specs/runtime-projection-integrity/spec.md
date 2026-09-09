## ADDED Requirements

### Requirement: Canonical final tool-call identity SHALL be unique within a run

Runtime event writing SHALL treat only `content.kind=function_call`, `partial=false`, and `metadata.lifecycle=tool_call_final` as a canonical final call fact. Partial tool-name/argument observations SHALL NOT count as call identity or execution. For each `(run_id, call_id)`, the Provider response path and durable boundary SHALL share one idempotent final-call identity path. The identity payload SHALL compare normalized tool name and canonical decoded arguments. An equivalent duplicate SHALL be a no-op; a different payload SHALL produce bounded `call_identity_conflict` and SHALL NOT silently overwrite the first fact. Each call SHALL execute at most once.

#### Scenario: Partial tool-call observations do not become final calls

- **WHEN** a streaming response emits one or more `partial=true` function-call observations before one final event
- **THEN** projections count only the final event as the call fact, and partial observations do not create an execution or duplicate diagnostic

#### Scenario: Provider response and durable boundary observe the same final call

- **WHEN** a Provider response recorder and `DurableToolBoundary` submit the same `(run_id, call_id, name, arguments)`
- **THEN** the canonical event stream and every derived model/session projection contain one final function-call fact, and the tool side effect executes once

#### Scenario: The same call identity has conflicting payload

- **WHEN** the same `(run_id, call_id)` arrives with a different tool name or normalized arguments
- **THEN** the runtime records a bounded `call_identity_conflict`, preserves the first fact, and does not execute or silently replace the call

#### Scenario: Distinct call identities are preserved

- **WHEN** one Provider response contains multiple final tool calls with distinct call IDs
- **THEN** each call ID has one canonical final fact and its own complete durable lifecycle

#### Scenario: The same call ID is repeated in a later model invocation

- **WHEN** the same `(run_id, call_id)` and normalized payload reappears under a different invocation ID
- **THEN** the durable operation result is reused, the side effect runs once, and no second operation record is created

### Requirement: Derived projections SHALL make historical duplicate calls deterministic

Session, Model Replay, resume, metrics and other canonical-derived projections SHALL use the same final-call identity rule when reading old stores. Equivalent historical duplicates SHALL not schedule or execute tools again and SHALL produce one Provider tool-call entry. The first equivalent fact in canonical order SHALL win. A non-equivalent duplicate SHALL remain diagnosable and SHALL fail closed: no Session/Model/Incremental/Provider projection SHALL emit an executable tool-call message for that conflicted identity.

#### Scenario: An existing session contains equivalent duplicate final calls

- **WHEN** a fresh process rebuilds a historical session containing two equivalent final function-call events for one call ID
- **THEN** the rebuilt session and Provider context contain one call entry, no second tool operation is created, and resume does not execute the tool again

#### Scenario: An existing session contains conflicting duplicates

- **WHEN** a historical session contains the same call ID with non-equivalent arguments
- **THEN** the rebuild exposes a bounded conflict diagnostic and does not choose a different payload silently
- **AND** Session, Model Replay, incremental replay and Provider context contain no executable tool-call entry for the conflicted identity

### Requirement: ArchiveRead SHALL distinguish valid EOF from an invalid range

`ArchiveRead` SHALL use Unicode character offsets for decoded text artifacts and byte offsets for binary artifacts. Every successful page SHALL return `ref`, `total_units`, `offset`, `next_offset`, `unit`, and `has_more`. The reader SHALL reject negative/non-integer offsets, invalid limits, and offsets greater than `total_units` with bounded `invalid_range` or `limit_exceeded` errors. An offset exactly equal to `total_units` SHALL be a valid empty EOF page.

#### Scenario: A valid bounded text page is requested

- **WHEN** `ArchiveRead` reads a text artifact with a non-negative offset below `total_units` and a limit within the configured maximum
- **THEN** it returns no more than the requested Unicode-character page and `next_offset` advances by the actual returned character count

#### Scenario: Unicode offsets are not byte offsets

- **WHEN** a text artifact contains multibyte UTF-8 characters and a page starts after one such character
- **THEN** the returned page starts at that character index, reports `unit=chars`, and does not split an encoded character

#### Scenario: A valid bounded binary page is requested

- **WHEN** `ArchiveRead` reads a binary artifact at a valid byte offset
- **THEN** it returns bytes, reports `unit=bytes`, and advances by the actual byte count

#### Scenario: An offset is beyond the artifact

- **WHEN** a 49,976-character text artifact is read with `offset=64,000`
- **THEN** `ArchiveRead` returns a bounded `invalid_range` error instead of a successful empty page

#### Scenario: The request is exactly at EOF

- **WHEN** a text or binary artifact is read with `offset` equal to `total_units`
- **THEN** it returns an empty page with `next_offset=total_units` and `has_more=false`

#### Scenario: The offset or limit is malformed

- **WHEN** `ArchiveRead` receives a negative/non-integer offset, a non-positive limit, or a limit above the capability maximum
- **THEN** it returns a stable bounded `invalid_range` or `limit_exceeded` error without publishing a page or archiving another result

### Requirement: Provider archive projection SHALL be recoverable and separate from terminal projection

Provider request projection SHALL preserve the Maka-aligned lifecycle: a newly completed safe result SHALL be materialized when the first Provider-specific projected request context can contain it; capacity rescue SHALL use a bounded prefix preview with its actual continuation offset, ref and callable `ArchiveRead` instruction; stale results SHALL use an actionable stable placeholder. The fit decision SHALL use the complete final Provider context envelope (system, messages, Provider-specific tools and tool-result shapes) and the frozen local formula `effective_window=int(model_context_window*0.70)`, `budget_bytes=max(0, effective_window*4)`. Binary previews MAY be base64 on the wire, but their continuation offset SHALL remain a byte offset. When a historical placeholder lacks `read_instructions`, the projection SHALL derive its instruction offset from `next_offset`, then `offset`, then `0`, and SHALL preserve a valid existing `limit`. A result SHALL NOT become a successful opaque reference when the current Agent has no scoped capability that can read it.

#### Scenario: The first Provider request has sufficient capacity

- **WHEN** a newly completed archived tool result fits the effective Provider request budget
- **THEN** both Anthropic and OpenAI-compatible final local SDK consumers receive the complete safe result rather than only `bounded_ref` metadata

#### Scenario: The first Provider request cannot contain the full result

- **WHEN** the complete safe result does not fit the effective first request budget
- **THEN** both Provider formats receive a bounded prefix preview, `truncated=true`, the actual character/byte continuation offset, the artifact ref, and an `ArchiveRead` instruction that matches an advertised callable capability

#### Scenario: Capacity rescue is measured against the complete request

- **WHEN** the surrounding system, assistant calls and other tool results consume most of the budget
- **THEN** the projection reduces preview units until the complete final Provider context envelope fits or returns a bounded failure, and it does not decide only from artifact size or the neutral projection size

#### Scenario: A stale result is projected after a later step

- **WHEN** a result is no longer the latest completed step
- **THEN** the final Provider request contains a bounded placeholder with ref and ArchiveRead instructions, and the full stale body is not silently re-inlined

#### Scenario: A stale placeholder remains stable as a suffix grows

- **WHEN** a valid stale `bounded_ref` has already been projected and a later request appends user, memory, or other non-tool context while the aggregate budget becomes tighter
- **THEN** the existing placeholder content, ref and ArchiveRead instructions remain unchanged; it is not rewritten to `capacity_exhausted` solely because the complete messages list no longer fits that placeholder candidate

#### Scenario: An Agent has no archive capability

- **WHEN** a Provider context is built for an Agent without a scoped `ToolResultArchiveCapability`
- **THEN** ArchiveRead is not advertised and the runtime does not publish a successful placeholder whose only recovery path is ArchiveRead

#### Scenario: The ArchiveRead envelope cannot fit the remaining budget

- **WHEN** the full projected message list cannot fit even after reducing the preview to the smallest actionable envelope
- **THEN** the final capacity gate prevents an over-budget SDK dispatch and emits a bounded capacity verdict; it does not rewrite multiple historical placeholders into tool-level errors. A tool-level `capacity_exhausted` is allowed only when the complete error envelope itself fits.

#### Scenario: The final capacity gate covers system, messages and tools

- **WHEN** the projected messages fit the local byte budget in isolation but the actual Provider request also contains system prompt or active tool definitions that exceed the same budget
- **THEN** the final capacity gate marks the request as exhausted, preserves the projected history, and does not call the Provider SDK with an over-budget context

#### Scenario: A capacity fallback is validated before dispatch

- **WHEN** a first-use full/preview candidate does not fit and the generated bounded error envelope also cannot fit after replacement
- **THEN** projection returns a bounded top-level capacity verdict instead of a tool-level error, the runtime does not send an over-budget error as a tool result, and canonical facts, artifacts and historical placeholders remain unchanged

#### Scenario: Final local consumers receive Provider content, not terminal status

- **WHEN** a local fake Anthropic SDK and a local fake OpenAI-compatible SDK receive the actual final Agent request
- **THEN** their captured tool content follows the Provider projection contract, is not replaced by terminal formatting, and an ArchiveRead page can be supplied to a later request

### Requirement: Terminal projection SHALL show human-readable bounded content

Terminal rendering SHALL use a projection independent from Provider wire content. For known `bounded_ref`, `archive_page`, and `archive_read_error` envelopes it SHALL parse the structured result, display bounded preview/page/content or error before/alongside metadata, and show concise ref/continuation or status. It SHALL NOT print JSON escaping as the only human-readable content, SHALL NOT hide useful content behind metadata-first truncation, and SHALL NOT hydrate the entire archive implicitly. The final terminal text SHALL remain within the existing display budget.

#### Scenario: A large read_file result is displayed

- **WHEN** terminal output receives a bounded archive result containing a preview and ref
- **THEN** the user sees the bounded text preview before or with metadata, truncation state, and a clear ArchiveRead continuation hint

#### Scenario: An ArchiveRead page is displayed

- **WHEN** terminal output receives a successful ArchiveRead result containing page content
- **THEN** the user sees page/content text and concise offset/has_more status rather than escaped JSON as the only content

#### Scenario: ArchiveRead returns an error

- **WHEN** ArchiveRead returns `invalid_range`, `scope_denied`, `session_mismatch`, `integrity`, or closed-store failure
- **THEN** terminal output shows a stable bounded error code/message and does not disclose a local path, traceback, or unrelated artifact data

#### Scenario: Terminal output is bounded and content-first

- **WHEN** a preview or page is larger than the 500-character terminal display budget
- **THEN** output stays bounded, retains the most useful prefix or error, and preserves a concise continuation/status marker when space permits

### Requirement: Run metrics SHALL preserve chronological boundaries

Run-level metrics SHALL use the first `invocation_opened` event in canonical ordinal order as `started_at_ms` and SHALL NOT replace it when later invocations open. `first_token_ms` SHALL be derived only from an explicit first-token action/lifecycle event, not from `partial=true` alone, and SHALL be non-negative when both timestamps exist. Run duration SHALL cover the terminal event. Invocation-level timing MAY be recorded separately and SHALL NOT be substituted for run-level timing.

#### Scenario: A run has multiple Provider invocations

- **WHEN** a run opens several invocations before reaching terminal
- **THEN** run `started_at_ms` remains the first invocation start and run `duration_ms` extends to terminal, while later invocation timing is separate

#### Scenario: Partial model output is not automatically first token

- **WHEN** an event is partial but has no explicit first-token action/lifecycle
- **THEN** it does not set run `first_token_at_ms` or `first_token_ms`

#### Scenario: The first token arrives after run start

- **WHEN** the explicit first-token timestamp is later than the first run opening timestamp
- **THEN** `first_token_ms` is non-negative and equals the elapsed time from run start

#### Scenario: Timing data is incomplete

- **WHEN** a run has no explicit first token or no terminal timestamp
- **THEN** the unavailable derived metric is null and is not calculated from a later invocation timestamp

#### Scenario: A run contains multiple terminal boundaries

- **WHEN** historical events contain more than one terminal status for the same run
- **THEN** metrics keep the first terminal timestamp and status, emit a bounded `multiple_terminal_events` error diagnostic, and do not mix the first timestamp with a later status

### Requirement: Archive and context facts SHALL remain immutable across projections

Provider hydration, terminal formatting, historical deduplication and ArchiveRead paging SHALL NOT rewrite canonical event facts, artifact bytes, metadata, refs or digests. ArchiveRead and projection failures SHALL remain bounded and fail closed; they SHALL NOT publish a successful ref that the current scoped capability cannot read.

#### Scenario: Provider materializes an archived result

- **WHEN** Provider projection reads an artifact to build a request context
- **THEN** the canonical event, artifact content, metadata, ref and digest remain unchanged

#### Scenario: A projection cannot read a required artifact

- **WHEN** a ref is missing, out of scope, integrity-invalid, or bound to a closed store
- **THEN** the projection returns a structured bounded error or a safe inline result when available, and does not claim successful recoverability

#### Scenario: Historical artifact compatibility preserves continuation

- **WHEN** an old valid `bounded_ref` lacks a `read_instructions` field but contains `next_offset=512`, `offset=256`, and `limit=64`
- **THEN** the Provider projection derives an ArchiveRead instruction with `offset=512` and `limit=64`, without rewriting the historical artifact

### Requirement: Canonical tool-result serialization SHALL use a normalized UTF-8 byte boundary

All tool-result ingress paths SHALL normalize the result before measuring it. Raw `bytes` SHALL become a JSON-safe Base64 binary envelope, and the measured value SHALL be the compact canonical JSON UTF-8 serialization, including JSON string quoting and escaping. The shared public limit SHALL be `16,777,216` bytes (16 MiB); a result at or below the limit SHALL pass and a result above it or not serializable SHALL produce a bounded `result_too_large` or `result_not_serializable` error without publishing an archive ref.

#### Scenario: Binary Base64 expansion is included in the boundary

- **WHEN** a binary result whose raw UTF-8 replacement length is below 16,777,216 expands beyond 16,777,216 after Base64 envelope normalization and canonical JSON serialization
- **THEN** the durable boundary rejects it as `result_too_large`, records no successful artifact ref, and does not expose the binary payload in the error

#### Scenario: Unicode and JSON escaping use UTF-8 byte size

- **WHEN** a result contains CJK, emoji, control characters, or JSON escaping that changes its serialized byte length
- **THEN** the boundary decision uses the exact normalized canonical JSON UTF-8 byte count rather than Unicode character count

#### Scenario: An exact byte boundary is accepted

- **WHEN** the normalized canonical JSON serialization is exactly 16,777,216 bytes
- **THEN** the result passes the common boundary and remains available as the complete canonical result

#### Scenario: A non-serializable result is bounded

- **WHEN** a tool returns a circular or otherwise non-JSON-serializable value
- **THEN** the boundary returns a stable `result_not_serializable` error without using `str(value)` as an under-counting substitute

### Requirement: New tool-result artifacts SHALL separate stable logical identity from content digest

New capability-owned tool-result artifacts SHALL derive a stable logical `artifact_id` from session, a stable canonical event key, tool call, normalized tool name, body digest and rewrite identity, and SHALL expose a ref that is distinct from the content `sha256`. When the tool message has no `name`, the projection SHALL resolve the tool name from the assistant `tool_calls` entry with the same `tool_call_id` before falling back to the neutral tool-result name. The current `run_id` and `parent_run_id` SHALL NOT participate in the logical ref identity, but MAY remain in metadata for authorization and diagnostics. The underlying content blob MAY be shared by digest, but logical metadata SHALL remain separate and authorization SHALL validate session/scope/integrity; a ref observed and registered from the current canonical history MAY be read across later runs in the same session, while an unregistered ref SHALL still require the allowed run/parent lineage. Legacy `artifact:sha256:<digest>` refs SHALL remain readable through their existing metadata without being rewritten.

#### Scenario: Identical content is archived in two sessions

- **WHEN** session A and session B archive the same tool-result body
- **THEN** they receive different logical refs with the same content digest, each session can read its own ref, and neither session can read the other's ref

#### Scenario: The same canonical result is projected in two chat runs

- **WHEN** the same session reprojects identical canonical tool-result content with the same runtime event ID, tool call ID and tool name under `run-a` and `run-b`
- **THEN** both projections receive the same logical ref and identical placeholder content, only one logical metadata record is created, and the second run can read the ref after it is registered from canonical history

#### Scenario: Replay shape omits tool name on the tool message

- **WHEN** two projections contain the same assistant `tool_calls` entry and canonical tool result, but one tool message omits `name` while the other includes it
- **THEN** both projections resolve the same tool name and receive the same logical ref and placeholder content

#### Scenario: A logical ref fails integrity validation

- **WHEN** a logical ref's metadata names a different digest/size than its content or its ref identity does not match metadata
- **THEN** inspection fails with a bounded integrity/metadata error and no page is returned

### Requirement: Artifact publication SHALL fail closed and roll back incomplete local state

Artifact publication SHALL return a successful ref only after local content, logical metadata, and any runtime-store metadata mirror have succeeded. If a later publication stage fails, files newly created by that call SHALL be removed where possible; a shared pre-existing content blob SHALL NOT be deleted. The failure SHALL never be converted into a successful canonical or Provider placeholder.

#### Scenario: Metadata commit fails after content write

- **WHEN** writing logical metadata fails after a new content blob was staged or published
- **THEN** the call returns an artifact metadata error, no successful ref is available, and the new content is absent or explicitly marked recovery-required

#### Scenario: Runtime-store mirror fails after local publication

- **WHEN** the runtime-store metadata mirror fails after local publication
- **THEN** the call does not return a ref; newly-created local logical metadata is rolled back without deleting a pre-existing shared blob

#### Scenario: ArchiveRead output is never recursively archived

- **WHEN** a projected `ArchiveRead` page or error is processed again
- **THEN** it is validated and passed through as a bounded result, and no new artifact ref is created from its envelope
