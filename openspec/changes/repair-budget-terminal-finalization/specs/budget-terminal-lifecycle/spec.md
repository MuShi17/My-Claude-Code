## Purpose

保证预算超限是可重放、可诊断且唯一的 Run terminal。

## ADDED Requirements

### Requirement: Budget terminal survives model finish

系统 MUST 能在 model call 已完成后记录 budget exceeded，而不再次调用已结束 recorder。

#### Scenario: Tool response exceeds turn budget

- **WHEN** provider 返回带工具调用的 response 且随后达到 cost/turn budget
- **THEN** canonical run 有一个 `budget_exceeded` terminal，且无 `model call has already finished` 错误

### Requirement: Terminal finalization is idempotent

重复 budget、late event 或 recovery close MUST 不产生第二个 terminal seal。

#### Scenario: Budget is observed twice

- **WHEN** 同一个 run 两次检测到相同 budget reason
- **THEN** store 保留唯一 terminal event，并返回可诊断的幂等结果
