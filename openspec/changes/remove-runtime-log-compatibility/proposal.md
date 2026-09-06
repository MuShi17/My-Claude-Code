## Why

Once canonical Agent emission, metrics, session recovery, and replay are closed, the remaining legacy logger/tracer compatibility surface only preserves a second authority and allows accidental fallback. Removing it makes the data boundary explicit: old files are retained outside the application, but no longer read or written.

## What Changes

- **BREAKING** Delete legacy logger/tracer implementation and runtime imports.
- Remove shadow sink, parity comparator, cutover/rollback route, and legacy mapping.
- Remove legacy RuntimeEvent normalization, legacy tool-result adapter, and legacy-only recovery helper.
- Remove legacy session discovery, old-root database discovery, and legacy CLI authority flags.
- Update tests, docs, packaging, and error messages to describe canonical-only behavior.

## Capabilities

### New Capabilities

- `canonical-runtime-compatibility`: Defines the absence of legacy runtime-log compatibility paths.

### Modified Capabilities

None.

## Impact

This is a breaking internal and CLI change affecting `logger.py`, `tracer.py`, event sinks, cutover, session/recovery helpers, artifact adapters, tests, documentation, and package exports. It does not delete user data.
