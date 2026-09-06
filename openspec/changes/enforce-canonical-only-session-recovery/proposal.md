## Why

The current CLI and session loader still discover legacy JSON/session files and can fall back to them when Canonical recovery fails. Maka treats recovery as a conservative projection over the canonical ledger, so corruption must be diagnosed rather than mistaken for missing canonical data.

## What Changes

- **BREAKING** Make list, latest, resume, and one-shot session behavior canonical-only.
- Keep session snapshots as disposable canonical-derived projections with source high-water and digest.
- Remove legacy session, flat JSON, and old-root SQLite discovery/fallback.
- Make corrupt/future/gapped canonical data fail closed with diagnostics.
- Recover stale runs conservatively without automatically replaying side-effecting tools.

## Capabilities

### New Capabilities

- `canonical-session-recovery`: Canonical-derived session, CLI, and conservative recovery behavior.

### Modified Capabilities

None.

## Impact

Changes session loading/listing, CLI resume/latest paths, recovery projection, snapshot metadata, and recovery/CLI tests. Existing legacy files are left untouched but are inaccessible to the application.
