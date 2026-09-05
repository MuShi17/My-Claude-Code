## Purpose

让 LLM capture 隐私策略在真实 Agent Loop 的所有输出路径上生效。

## ADDED Requirements

### Requirement: Capture off is body-free

当 capture mode 为 `off` 或 `metadata-only` 时，系统 MUST 不持久化 request/response body，legacy shadow 也 MUST 遵守该限制。

#### Scenario: Agent capture is off

- **WHEN** Agent 完成一次 provider 调用且 capture mode 为 `off`
- **THEN** legacy、canonical、artifact 和异常日志中均不存在 request/response body 或 secret marker

### Requirement: Capture references are valid

只有 body 已按允许策略成功归档时，系统 MUST 写入 `llm_ref`；失败或 off 状态不得伪造可读取引用。

#### Scenario: Redacted capture succeeds

- **WHEN** redacted body 被归档
- **THEN** response metadata 包含有效 ref/status，且归档内容已脱敏
