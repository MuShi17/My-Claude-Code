## 1. Projection model

- [x] 1.1 Define versioned Run/Turn/Session metrics and verify the projection accepts canonical event records without tracer input.
- [x] 1.2 Map token usage, first-token availability, provider finish, retries, permission waits, tool duration/outcome, and terminal status, and verify each mapping with fixtures.
- [x] 1.3 Add source high-water and digest to the derived snapshot and verify delete/rebuild equivalence.

## 2. Privacy and integration

- [x] 2.1 Bound metric labels and remove raw prompt, request, argument, and result data, and verify secret-marker negative tests.
- [x] 2.2 Integrate metrics with session/diagnostic consumers without making the projection an event writer, and verify failure classification.
- [x] 2.3 Establish comparison evidence against supported SessionTracer metrics before tracer removal.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and focused projection tests.
- [x] 3.2 Record unsupported historical tracer fields and the final metric mapping in the new batch Item 03.
