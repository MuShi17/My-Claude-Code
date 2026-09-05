## Context

`Agent._capture_legacy_llm` 先调用 `LLMCaptureManager`，随后无条件调用 `AgentLogger.save_llm_content()`。因此 manager 的 `off` 结果无法阻止 legacy raw write。

## Decisions

1. 将 capture policy 视为所有 sink 的统一门控。
2. `off` 和 `metadata-only` 都不得写 body；legacy api response 只写 capture status、usage、latency 和 shape metadata。
3. redacted 模式只能写经过统一 redaction 的 body/ref；历史文件保持只读。
4. 测试从 Agent 调用 `_capture_legacy_llm` 的真实集成入口，而不是只测 manager。

## Non-Goals

- 不支持 provider wire replay。
- 不改写历史日志。
- 不在本 change 切换 Canonical authority。
