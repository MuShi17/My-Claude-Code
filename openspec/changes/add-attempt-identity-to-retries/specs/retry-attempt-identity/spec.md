## Purpose

为 provider retry 建立稳定的尝试级关联。

## ADDED Requirements

### Requirement: Attempts have stable identities

每个实际 provider attempt MUST 有稳定 `attempt_id`，且相关事件必须复用该 identity。

#### Scenario: Provider retries once

- **WHEN** 第一次 provider 调用可重试失败，第二次调用成功
- **THEN** 两次 attempt 有不同 attempt_id，retry event 能分别关联前后 attempt

### Requirement: Attempt numbers do not replace identity

系统 MUST 同时保留顺序 attempt number 和 attempt_id，不能只用数字关联事件。

#### Scenario: Retry event is projected

- **WHEN** projection 读取 retry event
- **THEN** 结果包含 request/run/attempt identity 和 retry reason
