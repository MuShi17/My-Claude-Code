## Why

After canonical projections and durable tool operations are ready, the Agent Loop should no longer write the same lifecycle through AgentLogger and SessionTracer. Keeping both paths creates duplicate facts, privacy bypasses, and a continuing authority ambiguity.

## What Changes

- **BREAKING** Remove AgentLogger and SessionTracer from Agent and child-agent runtime construction.
- Emit provider, user, permission, tool, retry, error, and terminal facts only through the canonical emitter.
- Keep provider message arrays only as ephemeral request materialization rebuilt from canonical replay.
- Route LLM capture through the explicit off/metadata-only/redacted policy without legacy raw-body writes.
- Fail closed when a canonical append or terminal finalize required by the run fails.

## Capabilities

### New Capabilities

- `canonical-agent-logging`: Agent lifecycle emission uses only the canonical runtime event path.

### Modified Capabilities

None.

## Impact

Changes Agent and child-agent construction, provider loop integration, capture handling, lifecycle tests, and imports. Legacy implementation files remain until the separate compatibility-removal change lands.
