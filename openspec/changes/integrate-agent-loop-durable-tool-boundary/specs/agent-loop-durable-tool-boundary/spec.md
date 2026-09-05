## Purpose

统一 Anthropic 与 OpenAI 兼容 provider 的模型调用和工具执行生命周期，确保任何有副作用的工具都在执行前拥有可验证的 durable dispatch 记录，并支持流式、失败和预算边界的恢复。

## ADDED Requirements

### Requirement: Both providers expose one model lifecycle

Agent Loop MUST 对 Anthropic 和 OpenAI 兼容 provider 发射等价的 model invocation opened、request/metadata、stream/partial、final response、usage/latency、finish 或 error 语义；provider-specific wire shape 不得成为上层事实源。

#### Scenario: Anthropic completes with text

- **WHEN** Anthropic 流式调用正常返回文本和 usage
- **THEN** canonical lifecycle 包含 invocation、文本、usage/latency 和 completed finish，且可由同一 projector 读取

#### Scenario: OpenAI fails during streaming

- **WHEN** OpenAI 兼容 endpoint 在增量响应中失败
- **THEN** 已发生 partial 和最终 provider error 均有可关联记录，run 不被错误标记为 completed

### Requirement: Tool dispatch is durable before side effect

对于可能产生副作用的工具，系统 MUST 按 function call → permission decision → durable tool dispatch → tool execution → durable tool outcome → function response 的顺序处理；dispatch 未成功 durable 时 MUST 不执行工具。

#### Scenario: Permission is granted

- **WHEN** 模型请求一个获准工具
- **THEN** 在工具函数开始前已能从 store 读取带 call identity/args digest 的 tool dispatch 事件，执行后再写 outcome 和 function response

#### Scenario: Permission is denied

- **WHEN** 用户或权限策略拒绝工具
- **THEN** 系统记录拒绝决定和不执行结果，不写出声称真实副作用已发生的 outcome

### Requirement: Streaming tool calls remain bounded and non-executable

未完成的流式 tool arguments MUST 作为 partial 或 bounded snapshot 保存，不能被解释为最终 function call；只有 provider 明确完成且通过 validation/permission/dispatch 的调用才可执行。

#### Scenario: Arguments arrive in chunks

- **WHEN** tool arguments 分多次 delta 到达
- **THEN** partial 事件可供恢复，但在 final boundary 之前工具执行次数为零

#### Scenario: Malformed final arguments

- **WHEN** provider 结束 tool call 但 JSON/schema 参数非法
- **THEN** 写入 validation error/outcome，跳过工具执行并保持 invocation 可诊断

### Requirement: Usage and finish state are durable

每个 model call MUST 尽可能记录 provider/model、attempt、token usage、latency、finish reason、request shape hash 和错误类别；预算或取消中断也 MUST 产生可关联终态。

#### Scenario: Budget is exhausted after response

- **WHEN** 响应 usage 使 session 超过 max-cost 或 max-turns
- **THEN** usage 先 durable，再记录 budget_exceeded 终态，下一次工具执行或模型调用被阻止

#### Scenario: Provider returns an error before usage

- **WHEN** provider 失败且没有完整 usage
- **THEN** 记录 error、attempt、latency 和 available metadata，缺失 usage 使用明确 unknown 而非零值伪造

### Requirement: Agent Loop uses the canonical facade as source of truth

模型、工具和生命周期事实 MUST 通过 C04/C05 facade 发射；直接 `AgentLogger.log_*` 只能作为适配器内部的 legacy shadow，不得决定下一轮模型消息、工具是否执行或恢复结果。

#### Scenario: Legacy shadow is disabled

- **WHEN** diagnostic legacy sink 被关闭
- **THEN** canonical model/tool execution 和 projection 仍完整工作，且 Agent Loop 不因缺少旧文件而改变语义

#### Scenario: Canonical append fails before dispatch

- **WHEN** tool dispatch append 返回不可恢复错误
- **THEN** 工具不执行，run 进入明确错误/终态路径，legacy 不能掩盖 canonical failure
