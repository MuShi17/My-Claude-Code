## 1. Transition contract

- [x] 1.1 Define transition payload fields for target identity, replacement, reason, source high-water/digest, policy/projection versions, result digest, and context epoch, then verify malformed or stale transitions are rejected
- [x] 1.2 Add deterministic effective-context overlay logic at complete tool call/result group boundaries, then verify multi-tool, thinking, text, and tool ordering remain provider-valid

## 2. Durable activation

- [x] 2.1 Add SQLite atomic checkpoint-plus-transition persistence, then verify a commit failure leaves neither a newly active checkpoint nor activation event
- [x] 2.2 Route full compaction through the atomic activation path, then verify the new context is used only after the transaction commits and context epoch changes are observable
- [x] 2.3 Add recovery validation for checkpoint source coverage, digest, transition digest, and version mismatch, then verify corruption fails closed without rewriting canonical events

## 3. Lightweight compression integration

- [x] 3.1 Replace in-memory-only budget truncation with a durable replacement transition, then verify the next request and a restarted process use the same reduced result
- [x] 3.2 Replace stale snip and microcompact in-memory mutations with durable transitions, then verify old content is not restored by replay and retained tool pairs remain complete

## 4. Regression gates

- [x] 4.1 Add failure-injection tests for checkpoint write, transition append, commit, and replay verification, then verify the previous effective context remains active on failure
- [x] 4.2 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider`, `openspec validate --changes --strict --no-interactive`, and `git diff --check`; verify unrelated working-tree changes remain untouched

## 5. Post-audit correction gates

- [x] 5.1 Keep digest-covered compaction and replacement payloads stable through emitter preparation, then verify long effective contexts replay without a self-digest mismatch
- [x] 5.2 Require exact response-event targeting with an optional call-id consistency check, then verify duplicate call IDs across runs cannot redirect a replacement
