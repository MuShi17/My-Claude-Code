## 1. Context event contract

- [x] 1.1 Add and validate the provider-neutral memory context event shape, then verify text, source, digest, order, idempotency key, redaction, and bounded payload rules
- [x] 1.2 Extend Model Replay, Session, and Trace projections to identify memory context without treating it as original user text, then verify both providers receive the same logical memory content

## 2. Agent integration

- [x] 2.1 Replace Anthropic request-array memory mutation with commit-before-inject context event handling, then verify original user content is unchanged and the event is replayable
- [x] 2.2 Replace OpenAI-compatible request-array memory mutation with the same durable handling, then verify provider-specific messages contain memory exactly once
- [x] 2.3 Remove broad successful-consumption swallowing and add retry/idempotency handling, then verify Canonical write failures remain visible and retryable

## 3. Recovery and regression gates

- [x] 3.1 Add next-step, process-restart, duplicate-recall, delayed-recall, and redaction tests, then verify memory source/digest/order are preserved without raw diagnostic leakage
- [x] 3.2 Add failure-injection tests around event persistence and provider request preparation, then verify no uncommitted memory is marked consumed or injected as durable context
- [x] 3.3 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider`, `openspec validate --changes --strict --no-interactive`, and `git diff --check`; verify unrelated working-tree changes remain untouched
