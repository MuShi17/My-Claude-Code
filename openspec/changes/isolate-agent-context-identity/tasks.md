## 1. Identity and event model

- [x] 1.1 Extend `RunContext` and `RuntimeEvent` with validated context ownership fields and deterministic test defaults; verify domain serialization and validation tests pass
- [x] 1.2 Update identity-factory child/run helpers so every new execution receives a fresh run and invocation identity; verify duplicate opening events are rejected only for true identity reuse

## 2. Store and projection boundaries

- [x] 2.1 Add SQLite context columns/indexes/migration and `read_event_records(context_id=...)`; verify filtered reads preserve global ordinals and cold sequence validation
- [x] 2.2 Make cold and incremental model replay require and enforce an active context identity; verify sibling events never enter a context digest
- [x] 2.3 Bind replay cursor, memory injection, and transition diagnostics to context identity; verify a foreign cursor or transition fails closed

## 3. Agent integration

- [x] 3.1 Propagate root context, parent context, fresh child context, and parent run linkage through normal child Agents and forked skills; verify both paths can start after parent events exist
- [x] 3.2 Ensure resumed root turns allocate fresh run/invocation identities while preserving the session/context coordinate; verify recovery and two consecutive turns
- [x] 3.3 Add parent/child isolation integration tests covering child tool output, child compaction, sibling startup, and parent refresh; verify only the explicit parent tool result is visible

## 4. Verification

- [x] 4.1 Run the focused context/identity tests and the full `py313` test suite; verify all existing tests plus the new isolation scenarios pass
