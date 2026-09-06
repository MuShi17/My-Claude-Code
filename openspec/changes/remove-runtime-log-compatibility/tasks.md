## 1. Runtime removal

- [x] 1.1 Remove logger/tracer modules or exports after all callers are migrated, and verify package import/compile succeeds without them.
- [x] 1.2 Remove legacy sinks, mappings, cutover, parity, parser normalization, tool-result adapter, and legacy recovery helper, and verify static reference scans.
- [x] 1.3 Remove legacy session/database discovery and authority/rollback/approval CLI branches, and verify old flags are rejected.

## 2. Tests and documentation

- [x] 2.1 Delete or rewrite legacy compatibility tests and stale xfail while preserving canonical contract tests, and verify the focused suite remains green.
- [x] 2.2 Update current runtime documentation and package exports to describe canonical-only behavior, and verify links and examples point to the confirmed ISS001 plan.
- [x] 2.3 Run isolated HOME smoke and compare hashes of pre-existing legacy files before/after, verifying they are neither read nor mutated.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation, static import search, and the full py313 suite.
- [x] 3.2 Record the exact removed files, retained canonical projections, and old-data boundary in the new batch Item 06.
