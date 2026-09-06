## 1. Acceptance matrix

- [x] 1.1 Add canonical event, sequence, duplicate/conflict, terminal, late-event, partial, and projection-rebuild scenarios, and verify focused tests.
- [x] 1.2 Add tool permission, dispatch crash, uncertain outcome, and large-artifact scenarios, and verify no unsafe automatic retry.
- [x] 1.3 Add Anthropic/OpenAI replay scenarios for tool pairing, partial/hidden filtering, step order, and provider metadata, and verify both adapters.
- [x] 1.4 Add corruption, event gap, canonical append/finalize failure, and recovery classification scenarios, and verify controlled failure.

## 2. Boundary and smoke tests

- [x] 2.1 Add capture off/metadata-only/redacted secret-marker negative tests, and verify no raw body leakage.
- [x] 2.2 Add isolated HOME one-shot/list/latest/resume smoke, and verify no legacy directory or fallback access.
- [x] 2.3 Add forbidden runtime symbol/path scan and pre-existing legacy file hash comparison, and verify no legacy mutation.

## 3. Evidence and completion

- [x] 3.1 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider` and record exit code and focused results without hard-coded counts.
- [x] 3.2 Run `openspec validate --changes --strict --no-interactive` and `git diff --check`, and record results.
- [x] 3.3 Classify evidence tiers and remaining real-provider/production gaps, and verify the independent Gap Closure report contains no unsupported claims.
- [x] 3.4 Update the new batch, task card, Item 07, and confirmed ISS001 plan with final status and evidence links.
