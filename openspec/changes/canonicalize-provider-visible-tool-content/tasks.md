## 1. Contract and materializer

- [x] 1.1 Define a provider-visible tool-result representation and deterministic JSON encoder, then verify strings, nested mappings, arrays, Unicode, booleans, nulls, and non-finite numbers have explicit expected outputs
- [x] 1.2 Add explicit validation for supported content-block sequences, then verify arbitrary lists are not silently treated as valid Provider blocks
- [x] 1.3 Route redacted and bounded artifact results through the materializer, then verify secrets are absent and bounded placeholders retain ref, digest, size, and truncation metadata

## 2. Provider integration

- [x] 2.1 Route the first Anthropic and OpenAI-compatible tool-result request through the shared materializer, then verify no provider loop performs an independent JSON encoding
- [x] 2.2 Route Model Replay tool results through the same materializer and Provider adapters, then verify Anthropic receives only string or supported content-block tool results
- [x] 2.3 Preserve valid thinking/signature, text, tool-call, and multi-tool replay behavior while integrating the new boundary, then verify existing provider-shape tests pass

## 3. Regression and failure behavior

- [x] 3.1 Add request-capture tests comparing first materialization, next-turn replay, and SQLite close/reopen replay bytes for plain, nested, Unicode, and bounded-placeholder results
- [x] 3.2 Add negative tests for invalid block-like lists, wrapper objects, and unsupported values, then verify bounded diagnostics exclude raw values and secret markers
- [x] 3.3 Verify unsupported model-visible content enters the existing controlled-failure path before successful terminal finalization and does not append an invalid RuntimeEvent
- [x] 3.4 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider`, `openspec validate --changes --strict --no-interactive`, and `git diff --check`; verify unrelated working-tree changes remain untouched

## 4. Post-audit correction gate

- [x] 4.1 Keep long digest-covered replay content stable through canonical redaction preparation while retaining secret detection, then verify compaction and replacement transition digests
