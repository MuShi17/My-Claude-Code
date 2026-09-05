## Purpose

使真实 Agent Loop 的 canonical stream 包含完整、可重放的用户 turn。

## ADDED Requirements

### Requirement: Provider loops emit user events

Anthropic 和 OpenAI provider loop MUST 在发送首个 model request 前发射 canonical user event。

#### Scenario: One-shot user turn

- **WHEN** Agent 使用 fake provider 执行 one-shot
- **THEN** canonical stream 按 identity 包含 user event、model event 和终态事件

### Requirement: User and injected context remain distinguishable

memory injection、summary 和 tool result MUST 不覆盖或伪造原始 user event。

#### Scenario: Memory is injected

- **WHEN** 记忆预取结果被加入下一轮 provider context
- **THEN** projection 保留原始用户输入，并能识别注入内容的来源
