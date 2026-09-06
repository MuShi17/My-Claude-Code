## 1. Neutral compaction candidate

- [x] 1.1 Build complete neutral message groups from the active canonical replay and reject unresolved tool groups; verify calls/results never split
- [x] 1.2 Select summary range and retained tail only at group boundaries for both providers; verify last-event-tool-result and parallel-tool fixtures

## 2. Source-preserving checkpoint/reset

- [x] 2.1 Preserve retained `runtime_event_id` and tool identities in checkpoint context messages while assigning identities to synthetic summary messages; verify serialization round trips
- [x] 2.2 Update cold and incremental reset replay to retain source IDs and clear stale group indexes; verify warm/cold equality
- [x] 2.3 Support full-compaction → lightweight-compression → restart recovery using exact response event IDs; verify chained transition replay

## 3. Provider compaction integration

- [x] 3.1 Adapt Anthropic and OpenAI summarizer inputs from the neutral group candidate and preserve thinking/tool protocol validity; verify captured summarizer requests
- [x] 3.2 Refresh provider arrays only after committed activation and retain the previous context on failure; verify injected store failure behavior

## 4. Verification

- [x] 4.1 Add group-order, inverse completion, thinking, memory, and restart regression tests; verify cold/incremental/provider parity
- [x] 4.2 Run the focused compaction tests and full `py313` suite; verify all tests pass
