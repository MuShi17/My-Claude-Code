## Context

The first migration batch deliberately retained legacy JSONL, session fallback, shadow writes, rollback flags, and legacy adapters. The new canonical projections and recovery changes establish the replacement behavior, so the compatibility surface can now be removed as a separate, auditable step.

## Goals / Non-Goals

**Goals:**

- Make canonical-only the sole runtime route and data reader.
- Remove dead code and stale tests that keep the old authority alive.
- Preserve existing files by leaving them untouched on disk.

**Non-Goals:**

- Delete or rewrite user data.
- Remove canonical-derived snapshots, artifacts, metrics, or provider request materialization.
- Remove unrelated provider options such as `--thinking` merely because old help text described them poorly.

## Decisions

1. Delete compatibility modules only after the preceding changes prove their replacement behavior. This keeps the removal change mechanical and reviewable.
2. Remove CLI flags instead of silently accepting no-op compatibility options; invalid old flags must fail with normal argparse behavior.
3. Keep historical documentation in the first batch but mark it as superseded by the confirmed ISS001 plan. Runtime code and current docs describe only canonical behavior.
4. Do not add a conversion reader. If users need old data, it must be handled by an explicitly scoped offline export tool in a future change.
5. Use static import/reference scans plus isolated HOME tests to prove no old directory is created or read.

## Risks / Trade-offs

- [User-visible break] Old sessions cannot be resumed → document the boundary and retain files for manual handling.
- [Dead import] A hidden module may still import the removed logger → run package import, type/compile, and full test checks before completion.
- [Stale docs] First-batch docs still describe shadow as default → link the confirmed plan and mark the old batch historical.

## Migration Plan

Perform the deletion after canonical-only session/recovery passes. Update tests and docs in the same change. No database migration or filesystem cleanup is performed.
