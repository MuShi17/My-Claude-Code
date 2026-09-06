## Context

The existing trace path writes turn rows independently from the canonical store. Maka treats operational views as projections while keeping the semantic event log authoritative. The Python runtime already has event timestamps, token usage, tool lifecycle events, and run state that can support the required metrics.

## Goals / Non-Goals

**Goals:**

- Preserve useful diagnostics after tracer removal.
- Make metrics deterministic, bounded, versioned, and reconstructable.
- Distinguish missing canonical facts from projection or diagnostic output failures.

**Non-Goals:**

- Reproduce every historical tracer JSONL field when it has no canonical source.
- Add a remote telemetry backend or production SLO aggregation.

## Decisions

1. Compute metrics from finalized canonical events and durable tool operation state, using store order rather than wall-clock order for causality.
2. Store only a derived snapshot with projection version, source high-water, and source digest. The snapshot may be deleted and rebuilt.
3. Treat first-token latency, provider usage, retry attempts, permission waits, tool duration/outcome, and terminal status as the minimum stable metric set.
4. Keep raw sensitive request/response bodies out of metrics; expose only bounded metadata, hashes, and classifications.

## Risks / Trade-offs

- [Missing stream timestamps] A provider may not emit a first-token event → report an explicit unavailable value instead of guessing.
- [Projection drift] A new event version may change aggregation → version the projection and test rebuilds against fixtures.
- [High-cardinality data] Provider/tool names can grow without bound → keep bounded labels and full identity only in canonical refs.

## Migration Plan

Build the projection while SessionTracer remains available for comparison. After parity of supported metrics is established, remove tracer as a runtime dependency; do not read tracer files in the new projection.
