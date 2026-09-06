## 1. Operation model

- [x] 1.1 Define the operation identity, argument hash, recovery mode, and state transitions, and verify valid/conflicting identities in unit tests.
- [x] 1.2 Add the durable operation and journal schema in the runtime SQLite store, and verify indexes and transaction rollback behavior.

## 2. Lifecycle integration

- [x] 2.1 Persist dispatch before tool invocation and verify a failed dispatch write never calls the tool implementation.
- [x] 2.2 Persist outcome or controlled failure after execution and verify repeated matching outcomes are idempotent.
- [x] 2.3 Mark dispatch-without-outcome as `outcome_unknown` and verify recovery never automatically executes that operation.
- [x] 2.4 Archive oversized results before writing bounded references and verify hash, size, and archive failure behavior.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and focused tool-boundary, recovery, and artifact tests.
- [x] 3.2 Record the operation-state matrix and any unresolved side-effect limitations in the new batch Item 02.
