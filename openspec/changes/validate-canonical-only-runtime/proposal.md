## Why

Removing compatibility code is safe only when the canonical event contract, tool boundary, projections, recovery, privacy policy, and CLI behavior are verified together. A dedicated final change makes those cross-cutting gates explicit and prevents a green unit suite from hiding an integration regression.

## What Changes

- Add a canonical-only acceptance matrix spanning events, tools, replay, projections, recovery, privacy, and CLI.
- Add isolated HOME smoke for one-shot, list, latest, and resume.
- Add failure-injection checks for canonical append/finalize, corrupt storage, gaps, and uncertain tools.
- Add static checks proving legacy runtime paths are absent and old data is not mutated.
- Record evidence tiers and unresolved real-provider/production boundaries.

## Capabilities

### New Capabilities

- `canonical-only-runtime-acceptance`: Cross-cutting acceptance gates for the canonical-only runtime.

### Modified Capabilities

None.

## Impact

Adds acceptance tests, fixtures, validation scripts or commands, and documentation evidence. It does not add runtime behavior beyond test-visible assertions.
