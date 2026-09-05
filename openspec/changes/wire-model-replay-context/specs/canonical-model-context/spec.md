## Purpose

让 canonical events 成为真实下一轮模型上下文的事实源。

## ADDED Requirements

### Requirement: Next context is canonical-derived

真实 provider loop MUST 能从 canonical projection 生成下一轮 provider context，不能只依赖 legacy arrays。

#### Scenario: Legacy array is unavailable

- **WHEN** legacy message array 被清空但 canonical store 保留完整 prefix
- **THEN** 下一轮 context 仍能按 provider 约束生成

### Requirement: Replay diagnostics are visible

projection 遇到 unmatched tool、partial 或 hidden event MUST 返回可定位诊断，不得静默伪造完整 context。

#### Scenario: Tool result has no call

- **WHEN** canonical stream 只有 tool result 没有对应 call
- **THEN** context builder 返回 bounded diagnostic/placeholder，而不是可执行的伪造 tool call
