## Context

The session module currently knows both session-scoped canonical SQLite paths and legacy JSON/old-root paths. The CLI combines canonical and legacy sessions, and recovery has a legacy-only classification. Maka's recovery instead classifies the last canonical event and closes stale invocations without hot replay.

## Goals / Non-Goals

**Goals:**

- Make canonical SQLite the only application read source for session and recovery.
- Keep a disposable snapshot for fast display while making it rebuildable.
- Preserve conservative recovery and clear corruption diagnostics.

**Non-Goals:**

- Physical deletion, in-place migration, or conversion of existing legacy files.
- Automatic execution of tools during resume.
- Cross-machine continuation or distributed recovery.

## Decisions

1. Enumerate only session-scoped canonical databases. A missing canonical database means no canonical session; it does not authorize a legacy fallback.
2. Treat session snapshots as caches containing projection version, last canonical high-water, and digest. Rebuild them from events when missing or stale.
3. On corruption, future schema, identity mismatch, or event gap, preserve the database, emit a diagnostic, and fail the current operation closed.
4. Resume creates a new invocation at an explicit safe boundary and replays only canonical semantic history. An unresolved tool dispatch remains uncertain and is not re-executed.
5. Remove authority/rollback CLI flags once this change is ready; canonical is unconditional.

## Risks / Trade-offs

- [Breaking resume] Old sessions become unavailable → document the boundary and keep files untouched for manual export.
- [Rebuild cost] Large sessions take longer to list/resume → use bounded snapshots and indexed high-water reads.
- [Corruption visibility] A damaged database blocks resume → fail closed with a path-safe diagnostic rather than silently choosing another source.

## Migration Plan

Land after Agent Loop canonical emission and metrics are complete. Validate canonical snapshot rebuild and CLI smoke, then remove legacy discovery and fallback. Do not run a data migration.
