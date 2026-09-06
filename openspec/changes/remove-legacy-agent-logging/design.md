## Context

The Agent currently creates logger/tracer objects, subscribes legacy hooks, and flushes both at the end of chat. Child agents inherit logger-related settings. The canonical event emitter and provider context adapter already exist and should become the only runtime path.

## Goals / Non-Goals

**Goals:**

- Remove duplicate lifecycle writes and ensure child runs retain canonical lineage.
- Ensure provider context is materialized from canonical events before each request.
- Preserve privacy-safe opt-in LLM capture and fail-closed canonical durability.

**Non-Goals:**

- Delete compatibility modules in this change; that is handled after integration.
- Change provider API semantics or tool permission policy beyond their event emission.

## Decisions

1. Remove logger/tracer construction from the Agent lifecycle rather than adapting the logger to call the canonical store; this prevents a hidden second authority.
2. Keep the in-memory Anthropic/OpenAI arrays as request-local buffers only. Rebuild them from the replay projection at each provider boundary and never use them for resume.
3. Give child Agents the same canonical store and explicit parent/run lineage; do not pass legacy logger objects.
4. Treat canonical event append/finalize errors as run failures. Diagnostic-only projection failures may remain non-fatal and visible.
5. Keep LLM capture separate from the event ledger; capture bodies are never written through a legacy logger.

## Risks / Trade-offs

- [Behavior change] Some callers may import logger/tracer constructors → retain only temporary module compatibility until the removal change, while runtime imports are removed first.
- [Context drift] Provider arrays can be mutated by old code → add a test that clears/perturbs them before the next request.
- [Durability failure] A failed terminal append can mask the provider result → return controlled failure and preserve the diagnostic cause.

## Migration Plan

Land after contract, tool operation, and metrics changes. Run provider/child/retry/cancel/capture tests, then let the compatibility-removal change delete unused files and flags.
