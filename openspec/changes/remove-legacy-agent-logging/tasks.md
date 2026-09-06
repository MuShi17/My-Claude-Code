## 1. Agent lifecycle

- [x] 1.1 Remove logger/tracer construction, hooks, flush, and close calls from the Agent lifecycle, and verify imports and normal chat tests no longer require them.
- [x] 1.2 Pass canonical store, parent run, and lineage to child Agents without legacy logger configuration, and verify child/skill/retry identity tests.
- [x] 1.3 Route provider, user, permission, error, retry, budget, cancel, and terminal facts through the canonical emitter, and verify lifecycle fixtures.

## 2. Provider and capture boundaries

- [x] 2.1 Rebuild provider request context from canonical replay at each request boundary, and verify stale/cleared message buffers do not affect requests.
- [x] 2.2 Keep LLM capture independent of legacy logging, and verify off/metadata-only/redacted secret-marker tests.
- [x] 2.3 Propagate canonical append and finalize failures as controlled run failures, and verify no fallback writer is called.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and focused Agent/provider/child/capture tests.
- [x] 3.2 Record the runtime import/dependency inventory in the new batch Item 04 before deleting compatibility modules.
