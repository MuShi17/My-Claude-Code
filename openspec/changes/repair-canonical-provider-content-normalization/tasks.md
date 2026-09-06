## 1. Boundary normalization

- [x] 1.1 Add a provider-response text normalizer that accepts strings, handles the explicitly valid empty signed-thinking case, and returns a typed rejection for other values; verify unit tests cover `str`, `None`, mapping, list, and SDK-wrapper inputs.
- [x] 1.2 Route Anthropic text and thinking response blocks through the normalizer before calling `ModelCallRecorder`; verify valid text, thinking, signature, and tool-call blocks produce the same canonical events as before.
- [x] 1.3 Audit the OpenAI-compatible response recording path and apply the same canonical text/reasoning boundary without changing valid tool-call argument handling; verify text and reasoning content remain string-only in recorded events and provider reasoning is replayable.

## 2. Controlled failure and diagnostics

- [x] 2.1 Define a bounded rejected-content diagnostic containing provider, block kind, response index, and safe value type metadata; verify raw values, serialized payloads, and secret markers are absent.
- [x] 2.2 Convert rejected provider content into the existing Canonical controlled-failure path before tool execution or successful terminal finalization; verify no invalid event is appended and the run is not reported successful.
- [x] 2.3 Preserve prior thinking signature and tool replay behavior when normalization succeeds; verify signed thinking remains replayable and malformed blocks cannot create dangling tool-use history.

## 3. Regression and acceptance gates

- [x] 3.1 Add unit tests for normal text, signed thinking, empty signed thinking, long canonical text, and every rejected non-string category; verify the focused runtime test module passes in `py313`.
- [x] 3.2 Add Agent integration tests for malformed Anthropic-compatible text/thinking responses, a valid multi-tool response, and an OpenAI-compatible reasoning/tool-call step; verify canonical events, diagnostic fields, terminal status, and replay ordering.
- [x] 3.3 Run `D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider`, `openspec validate --changes --strict --no-interactive`, and `git diff --check`; verify all gates pass without overwriting unrelated working-tree changes.
- [x] 3.4 Re-run the DeepSeek vulnerable-secret smoke and inspect the canonical runtime outcome; verify the previous `content.text` validation crash is absent, valid provider turns continue, and any remaining provider-shape rejection is explicitly diagnosed rather than silently falling back.
