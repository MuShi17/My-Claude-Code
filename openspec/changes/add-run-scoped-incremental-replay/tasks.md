## 1. Cursor and source API

- [x] 1.1 Add a run-scoped replay cursor with source high-water/digest, projection version, context epoch, prior prefix, pending reducer state, and diagnostics, then verify cursor fields round-trip safely
- [x] 1.2 Add an ordinal-bounded incremental event read path while retaining a full cold-read path, then verify read counts and ordering for empty, single-event, and multi-event suffixes

## 2. Incremental projection

- [x] 2.1 Implement current-turn suffix projection using the existing reducer semantics, then verify incomplete calls remain pending and are not fabricated as provider messages
- [x] 2.2 Preserve multi-tool, thinking/signature, text continuation, and tool-result pairing across suffix updates, then verify Anthropic and OpenAI provider shapes remain valid
- [x] 2.3 Invalidate and reinitialize the cursor on committed context epoch/transition changes, then verify reduced history is not resurrected and old prefixes are not reused

## 3. Agent integration and observability

- [x] 3.1 Replace normal per-step full replay in both provider loops with cursor-based incremental replay while retaining controlled cold rebuild fallback, then verify normal steps consume only new ordinals
- [x] 3.2 Emit prefix digest, high-water, epoch, read count, projection duration, warm/cold status, and rebuild reason without raw request content, then verify diagnostics are bounded

## 4. Equivalence and performance gates

- [x] 4.1 Add fixtures that compare warm incremental and cold replay provider-neutral messages, provider wire structures, digests, and diagnostics across tools, compaction, memory, and restart boundaries
- [x] 4.2 Add a long multi-step fixture/benchmark proving normal replay reads fewer prior events than full rebuild while retaining an explicit cold-path correctness check
- [x] 4.3 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider`, `openspec validate --changes --strict --no-interactive`, and `git diff --check`; verify unrelated working-tree changes remain untouched

## 5. Post-audit correction gates

- [x] 5.1 Hold parallel tool groups until all known results are available, then verify inverse completion order still matches cold replay and model-call order
- [x] 5.2 Track response identities as ordered event-id/call-id pairs and bind compression capture to event IDs, then verify repeated call IDs remain run-scoped
- [x] 5.3 Skip full-ledger sequence validation on ordinal suffix reads, then verify the warm path does not perform hidden O(history) validation work
