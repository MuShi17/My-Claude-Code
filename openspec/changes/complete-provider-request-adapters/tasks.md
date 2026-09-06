## 1. Wire-format helpers

- [x] 1.1 Add deterministic OpenAI function-call and tool-result constructors; verify arguments are JSON strings and arbitrary mappings never become raw `content`
- [x] 1.2 Strip neutral runtime/projection metadata at the provider boundary while retaining internal source identity; verify direct adapter payload assertions

## 2. Provider integration

- [x] 2.1 Replace OpenAI neutral tool-call passthrough with strict provider conversion for one and many calls; verify model-order and pairing behavior
- [x] 2.2 Preserve Anthropic signed thinking, multi-tool grouping, and string/list tool-result validity; verify thinking signatures and immediate results
- [x] 2.3 Route first-use and replay/recovery requests through one adapter path; verify no provider loop constructs a second incompatible shape

## 3. Regression coverage

- [x] 3.1 Add mock-transport captures for OpenAI and direct Anthropic request assertions, including nested results, bounded placeholders, thinking, and recovery
- [x] 3.2 Compare cold, warm, and reopened-store provider messages for identical canonical high-water; verify wire-level equivalence
- [x] 3.3 Run focused provider tests and the full `py313` test suite; verify all tests pass
