## ADDED Requirements

### Requirement: Unified canonical prune estimation

Provider projection SHALL estimate tool-result prune size from the result normalized by the existing canonical_tool_result_bytes entry point. It SHALL decode that deterministic JSON UTF-8 representation as serialized text and calculate estimated_tokens = ceil(serialized_character_count / 3). The serialized character count SHALL be the number of Python Unicode code points and SHALL include JSON structure, escaping and any normalized Base64 binary envelope. This estimate SHALL NOT replace Provider request byte measurement.

#### Scenario: Result at the ordinary threshold is retained

- **WHEN** a canonical result serializes to 6144 characters and has no active semantic supersession decision
- **THEN** its estimated size SHALL be 2048 tokens and stale/active ordinary pruning SHALL retain it

#### Scenario: Result one character beyond the threshold is eligible

- **WHEN** a canonical result serializes to 6145 characters and belongs to an eligible stale or active step
- **THEN** its estimated size SHALL be 2049 tokens and it SHALL be eligible for archive replacement

#### Scenario: Normalization and binary representation are included

- **WHEN** a result contains JSON escaping, CJK/emoji or bytes normalized into a Base64 envelope
- **THEN** the estimator SHALL measure the canonical serialized representation rather than terminal display text or pre-normalized input

#### Scenario: Provider byte fit remains independent

- **WHEN** the prune estimator calculates a result size
- **THEN** final Provider fit SHALL still measure the context-bearing provider envelope in UTF-8 bytes

### Requirement: Historical stale tool-result pruning

The projection SHALL order turns by the first canonical ordinal at which each turn appears and protect the newest two turns from ordinary stale pruning. A raw result from an older turn SHALL be replaced only when its estimated size is greater than 2048 tokens and archive publication succeeds.

#### Scenario: Large result outside the protected turns is archived

- **WHEN** a complete tool result belongs to a turn older than the newest two turns and estimates above 2048 tokens
- **THEN** the Provider projection SHALL replace it with a stable placeholder containing its ref, integrity identity and actionable ArchiveRead instructions

#### Scenario: Small old result remains inline

- **WHEN** a complete tool result belongs to an unprotected old turn and estimates at or below 2048 tokens
- **THEN** the projection SHALL keep the complete result inline

#### Scenario: Recent large result is not stale-pruned

- **WHEN** a complete tool result estimates above 2048 tokens but belongs to one of the newest two turns
- **THEN** stale pruning SHALL leave it unchanged and SHALL NOT archive it only because the aggregate request is large

#### Scenario: Turn ordering does not use identifier text

- **WHEN** turn identifiers sort lexicographically differently from their first canonical ordinals
- **THEN** the newest-two protection SHALL follow canonical ordinal order rather than turn identifier string order

### Requirement: Active current-turn step pruning

The projection SHALL treat each completed Provider tool-call group identified by the same run_id and invocation_id as one current-turn step. During the ordinary active pass, a result from an older completed step in the active turn SHALL be archived when it exceeds 2048 estimated tokens; the newest completed step SHALL remain complete while it remains the newest.

#### Scenario: Earlier current-turn step is pruned before the next request

- **WHEN** the active turn has two completed tool-call steps and the earlier step exceeds 2048 estimated tokens
- **THEN** the earlier step SHALL receive a stable ArchiveRead-capable placeholder while the newest step remains complete

#### Scenario: The only completed current-turn step is protected

- **WHEN** the active turn has only one completed tool-call step whose result exceeds 2048 estimated tokens
- **THEN** the ordinary active pass SHALL keep that result complete

#### Scenario: A newer step makes the previous step eligible

- **WHEN** a new completed step is appended to the active turn
- **THEN** the previous newest step SHALL become an older active candidate on the next ordinary pass, subject to the 2048 threshold and archive success

#### Scenario: Repeating the same projection is idempotent

- **WHEN** the same canonical current-turn source is projected again after active pruning
- **THEN** existing placeholders and archive refs SHALL remain byte-for-byte stable and no duplicate artifact publication SHALL occur

### Requirement: Request-cycle emergency includes the latest active step exactly once

The Agent SHALL own a Provider request cycle and SHALL create one non-wire cycle identity before context refresh for one intended Provider dispatch. The identity SHALL bind the session, Provider request id, canonical source digest/high-water, provider, active turn, system/tools digest and budget. Ordinary projection, emergency projection and final verdict SHALL share that identity. The runtime SHALL first build the ordinary projection with the newest active step protected. If the context-bearing final Provider envelope does not fit and emergency_used is false, it SHALL execute exactly one emergency active pass that includes the newest completed step, then set emergency_used true. If that emergency projection still does not fit, the runtime SHALL fail closed before SDK dispatch.

#### Scenario: An ordinary fitting request does not run emergency

- **WHEN** the ordinary final Provider context envelope fits its explicit budget
- **THEN** the runtime SHALL not archive the newest active step solely to reduce size and SHALL record no active emergency

#### Scenario: Oversized latest step is pruned only after the first fit failure

- **WHEN** the ordinary active projection keeps the newest step and the final Provider context envelope exceeds budget
- **THEN** the same request cycle SHALL execute at most one emergency pass and SHALL allow the latest step to be archived when it exceeds 2048 estimated tokens

#### Scenario: Repeated refresh in one cycle does not repeat emergency

- **WHEN** Agent context refresh is called again with the same cycle identity after the emergency pass
- **THEN** the runtime SHALL reuse the existing projection outcome and SHALL not publish another artifact or execute another emergency pass

#### Scenario: A new Provider request starts a new cycle

- **WHEN** a new Provider request id, canonical high-water, or completed active step is created
- **THEN** the Agent SHALL create a new request cycle and SHALL re-evaluate ordinary protection from canonical source

#### Scenario: No budget or no active metadata skips emergency

- **WHEN** no Provider byte budget or no active turn metadata is available
- **THEN** the runtime SHALL skip emergency pruning and SHALL not infer capacity pressure from aggregate message length

#### Scenario: Capacity remains exhausted after emergency pruning

- **WHEN** the emergency projection still exceeds the Provider byte budget
- **THEN** the runtime SHALL expose a bounded provider_capacity_exhausted verdict, SHALL perform zero SDK dispatches, and SHALL not rewrite existing placeholders into capacity_exhausted tool errors

### Requirement: Conservative active-step semantic supersession

Semantic supersession SHALL be limited to completed active steps in the same current turn. The exact duplicate identity SHALL be semantic tool name, canonicalized input, success/error status and canonical body SHA-256; it SHALL not include tool_call_id. A newer step SHALL replace an older result only when the older result estimates at least 256 tokens and the identity proof is deterministic. Parallel calls, unresolved failures, unsupported tools and incomplete identities SHALL remain visible.

The supported My-Claude-Code semantic mapping SHALL be:

- read_file to Read, with file_path, offset default 0 and limit default positive infinity;
- list_files to Glob, with pattern and normalized path/cwd;
- grep_search to Grep, with pattern, normalized path and include;
- run_shell to Bash only for foreground shell-free read-only git status, diff, log, show, branch or rev-parse queries; all other shell results SHALL not be treated as snapshots.

Path normalization SHALL remove only explicit leading ./ segments and SHALL not collapse backslashes, UNC prefixes or repeated slashes. The change SHALL not implement Maka failure_resolved supersession.

#### Scenario: A newer identical result supersedes an older result

- **WHEN** a newer active step has the same semantic tool name, canonical input, success/error status and body hash as an older result, and the older result estimates at least 256 tokens
- **THEN** the older result SHALL be archived with a bounded exact_duplicate reason and the newer result SHALL remain complete

#### Scenario: A newer read covering the old range supersedes the old read

- **WHEN** a newer successful read_file call has the same normalized file path and its range starts no later than the older offset and ends no earlier than the older range, and the older result estimates at least 256 tokens
- **THEN** the older result SHALL receive a bounded newer_read_covers_range placeholder

#### Scenario: A newer supported snapshot supersedes an older snapshot

- **WHEN** a newer successful list_files, grep_search or eligible foreground read-only git run_shell call has the same normalized snapshot key and a strictly newer active step
- **THEN** the older result SHALL receive a bounded newer_snapshot placeholder when it estimates at least 256 tokens

#### Scenario: Small or ambiguous results are not superseded

- **WHEN** the older result is below 256 estimated tokens or the identity proof is incomplete
- **THEN** the projection SHALL retain the older result and SHALL not guess a supersession

#### Scenario: Parallel or failure transitions are not guessed

- **WHEN** calls are parallel, a failure is being used to replace a success, or a tool is outside the supported semantic mapping
- **THEN** the projection SHALL retain the older result and SHALL not apply semantic supersession

### Requirement: Archive failure is fail-open and diagnostics are bounded

The projection SHALL return an immutable archive outcome containing messages, projection phase, emergency_used, request size/budget and sidecar ProjectionDiagnostic values. ProviderContext diagnostics SHALL merge canonical replay diagnostics before archive diagnostics, deduplicate by code/event_id/call_id, sort by canonical ordinal and message encounter order, and retain at most 32 entries. Each diagnostic message SHALL be at most 256 Unicode characters and SHALL not contain raw result bodies, artifact contents or complete exception text.

The stable diagnostic codes SHALL include archive_write_failed, archive_identity_unavailable, replay_metadata_unavailable, synthetic_message_unclassifiable, active_emergency_used and provider_capacity_exhausted. Archive failure and identity failure SHALL be fail-open warnings; provider_capacity_exhausted SHALL be a final verdict and SHALL not enter Provider messages. If final capacity fails before ProviderContext can be returned, ProviderCapacityError or its equivalent SHALL carry the same bounded diagnostics, cycle identity, request size and budget.

#### Scenario: Archive publication fails without losing raw content

- **WHEN** an eligible result exceeds the prune threshold but archive publication fails
- **THEN** the Provider-visible content SHALL remain the original result, no successful ref or metadata SHALL be emitted, and archive_write_failed SHALL be available out of band

#### Scenario: Existing failure regression expects raw preservation

- **WHEN** artifact.write or archive.write is fault-injected for an eligible projection
- **THEN** the regression test SHALL assert raw content preservation, no artifact metadata publication and a bounded diagnostic that does not enter Provider messages

#### Scenario: Missing identity fails safely

- **WHEN** a raw result lacks the canonical runtime event, ordinal, tool-call identity, required tool name or body identity
- **THEN** the projection SHALL disable only the capabilities requiring that field according to the identity matrix, SHALL not claim unsafe ref reuse or semantic supersession, and SHALL expose the corresponding bounded diagnostic

#### Scenario: Diagnostics are stable across repeated reads

- **WHEN** the same request cycle is projected repeatedly with the same archive failure or identity deficiency
- **THEN** diagnostics SHALL have stable order and content and SHALL not be duplicated

### Requirement: Archive placeholders and ArchiveRead envelopes are stable

A successful placeholder SHALL preserve its ref, body hash, byte size, continuation hint and actionable ArchiveRead instruction across later projections. Existing valid placeholders and ArchiveRead page, bounded-ref and error envelopes SHALL not be treated as raw prune candidates.

#### Scenario: Existing placeholder survives later suffixes

- **WHEN** a valid placeholder is projected again after additional user, memory or tool messages are appended
- **THEN** its Provider-visible content SHALL remain unchanged and SHALL not become capacity_exhausted

#### Scenario: Cross-run projection reuses the stable artifact

- **WHEN** the same canonical event, tool call and result body are projected under different Agent chat run identifiers
- **THEN** the placeholder and logical archive ref SHALL be identical and only one logical artifact metadata record SHALL be used

#### Scenario: ArchiveRead output is not recursively archived

- **WHEN** a tool result already contains an ArchiveRead page, bounded ref or ArchiveRead error envelope
- **THEN** the projection SHALL validate or pass through that envelope without creating another archive ref

### Requirement: Canonical tool-call arguments have one semantic representation

新写入的 canonical final tool-call event SHALL 将可成功解码的 JSON object string 参数规范化为 object，并继续执行既有 redaction。Replay SHALL 对历史 JSON string 参数使用同一 decoder，以便 OpenAI 和 Anthropic 的 semantic descriptor 使用相同 canonical input。非法 JSON 或非 object 参数 SHALL 保留原值并将 semantic input 标记为 incomplete；不得静默替换为空 object。

#### Scenario: OpenAI JSON-string arguments participate in semantic pruning

- **WHEN** OpenAI final tool-call contains a valid JSON object string and the call belongs to an eligible active step
- **THEN** replay metadata SHALL expose a complete semantic descriptor, and duplicate/read-range/snapshot supersession SHALL use the decoded object input

#### Scenario: Invalid tool arguments remain conservative

- **WHEN** a final tool-call argument is invalid JSON or decodes to a non-object
- **THEN** the canonical event SHALL retain the safe original value, semantic input SHALL be incomplete, and the result SHALL not be semantically superseded

### Requirement: Existing archive envelopes enforce integrity and bounded structure

When an existing `bounded_ref` is projected, the runtime SHALL authorize and inspect its ref, SHALL compare any supplied `sha256` and `size_bytes` with artifact metadata, and SHALL return a bounded integrity error on mismatch. Missing legacy integrity fields MAY be filled from the same authorized metadata. Existing `archive_page` and `archive_read_error` values SHALL satisfy their bounded structural contracts before pass-through; malformed values SHALL become bounded `invalid_archive_read` or `integrity_mismatch` errors without raw malformed fields.

#### Scenario: Tampered placeholder claims are rejected

- **WHEN** an existing placeholder has a valid authorized ref but a sha256 or size_bytes claim that differs from artifact metadata
- **THEN** projection SHALL preserve neither incorrect claim and SHALL return a bounded integrity mismatch for that ref

#### Scenario: Legacy placeholder claims are repaired from metadata

- **WHEN** an authorized legacy placeholder omits sha256 or size_bytes but its ref resolves to valid artifact metadata
- **THEN** projection SHALL fill the missing claim from metadata and SHALL retain the same ref and readable ArchiveRead continuation

#### Scenario: ArchiveRead page and error envelopes are structurally bounded

- **WHEN** an ArchiveRead page/error result has an invalid ref, hash, size, range, unit, error type, message or instruction field
- **THEN** projection SHALL return a bounded validation error, SHALL not recursively archive the result, and SHALL not expose the malformed field values

#### Scenario: Declared bounded refs never fall back to raw archiving

- **WHEN** a tool result declares `kind=bounded_ref` but its ref is missing or syntactically invalid
- **THEN** projection SHALL route it through bounded-envelope validation, SHALL return `invalid_archive_read` or `integrity_mismatch`, and SHALL not publish another artifact

#### Scenario: ArchiveRead page content is verified against the artifact

- **WHEN** an ArchiveRead page has a structurally valid ref/hash/size but its page body, offset range, total units or `has_more` differs from the authorized artifact read for that range
- **THEN** projection SHALL return a bounded `integrity_mismatch` without the forged page fields

#### Scenario: ArchiveRead optional fields remain bounded

- **WHEN** a page/error envelope contains an unknown field or an oversized `read_instructions`, `preview`, `detail` or page body
- **THEN** projection SHALL return a bounded `invalid_archive_read` without passing through the oversized or unknown value

### Requirement: Cold and incremental replay use sidecar metadata without wire leakage

Cold replay and incremental replay SHALL expose the same internal turn/step metadata for the same canonical event prefix. Compaction reset, cursor reopen and recovery rebuild SHALL reconstruct sidecar metadata from canonical events or mark synthetic messages as unclassifiable. Metadata SHALL be stored outside Provider messages, and Anthropic/OpenAI conversion SHALL copy only provider-allowed fields.

#### Scenario: Equivalent replay paths produce equivalent pruning

- **WHEN** the same canonical event prefix containing at least three historical turns and multiple current-turn steps is built through cold replay and incremental replay
- **THEN** both Provider projections SHALL produce identical visible messages, sidecar metadata, placeholder refs, ordering and artifact publication counts

#### Scenario: Compaction reset and reopen preserve parity

- **WHEN** the same canonical SQLite event set is projected before and after compaction reset, cursor reopen and recovery rebuild
- **THEN** turn/step classification, identity state, bounded diagnostics, stable refs and Provider-visible messages SHALL remain equivalent

#### Scenario: Internal classification metadata is hidden from the Provider

- **WHEN** a Provider request is built after sidecar turn/step metadata has been attached
- **THEN** the serialized Anthropic/OpenAI context envelope SHALL contain no internal turn_id, run_id, invocation_id, step_key, ordinal, identity_state or metadata wrapper fields while retaining legal Provider tool-call identifiers

### Requirement: Final capacity and canonical boundaries remain authoritative

The runtime SHALL measure the context-bearing Provider envelope in UTF-8 bytes using the fixed field set: Anthropic system, tools, messages and provider content wrappers; OpenAI-compatible system message, tools, messages and provider message/tool wrappers. Model, stream, stream_options, HTTP headers and other transport fields SHALL remain outside context-byte measurement; max_tokens/output reserve SHALL be handled by the existing budget policy. Prune estimated tokens SHALL not replace this measurement. The public canonical tool-result limit SHALL remain 16 MiB after normalization.

#### Scenario: Public result at the 16 MiB boundary is preserved

- **WHEN** a normalized canonical result is exactly 16 MiB
- **THEN** the canonical boundary SHALL accept it subject to existing serialization rules

#### Scenario: Public result beyond the 16 MiB boundary is rejected

- **WHEN** a normalized canonical result exceeds 16 MiB by one byte
- **THEN** the canonical boundary SHALL reject it before it becomes a complete canonical tool result

#### Scenario: Final Provider capacity blocks dispatch

- **WHEN** the context-bearing Provider envelope remains over its byte budget after all permitted projection passes
- **THEN** the SDK dispatch count SHALL be zero, the bounded capacity verdict SHALL be readable by the local Agent consumer, and the canonical event log SHALL remain unchanged
