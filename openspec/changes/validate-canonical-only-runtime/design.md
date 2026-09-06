## Context

The first batch's evidence validated canonical cutover while compatibility remained available. This batch must validate the stricter post-removal boundary and distinguish local fake-provider/fixture evidence from real network, multi-process, or production evidence.

## Goals / Non-Goals

**Goals:**

- Prove the canonical ledger and projections work without legacy files or writers.
- Exercise integration failure paths and privacy-negative scenarios.
- Make evidence reproducible in the `py313` environment.

**Non-Goals:**

- Claim production readiness from local tests.
- Add a deployment or real external-provider test dependency to the default suite.

## Decisions

1. Use a temporary HOME and fake providers for deterministic CLI and provider tests; optionally run real-provider checks only when credentials and environment are explicitly supplied.
2. Run static scans for forbidden runtime symbols and old path construction in addition to behavior tests.
3. Verify pre-existing legacy file hashes remain unchanged, without opening them through the application.
4. Require controlled failure evidence for canonical durability and corruption; a passing fallback is a failure of this acceptance change.
5. Do not hard-code the total test count; record commands, exit codes, and relevant focused results.

## Risks / Trade-offs

- [False confidence] Fake providers cannot prove network behavior → label evidence tiers and leave real-provider scope explicit.
- [Test coupling] Static symbol scans can be brittle → scope scans to runtime imports and path construction, not historical docs.
- [Breaking boundary] Old data becomes inaccessible → document and verify non-mutation rather than adding a hidden reader.

## Migration Plan

Run after all six preceding changes. If any gate fails, keep the implementation in the pre-removal state or revert only the new cleanup change; do not restore a silent legacy fallback. Record Gap Closure before any delivery action.
