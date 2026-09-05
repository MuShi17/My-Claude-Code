## Purpose

使需求批次、OpenSpec 工件、实现、测试和验收报告在最终收口时保持一致。

## ADDED Requirements

### Requirement: Completion status is evidence-backed

批次和 change 的完成状态 MUST 由可复现命令、测试或文件证据支持，不能只依赖 tasks checkbox。

#### Scenario: Batch is ready to close

- **WHEN** R-01 至 R-10 的 Gate 全部通过
- **THEN** 总览、任务卡、Item、OpenSpec 和验收报告的 status/checkbox/evidence 一致

### Requirement: Blockers remain visible

任何未通过 P0/P1 Gate MUST 在验收报告和批次总览中明确保留，不能以测试全绿覆盖。

#### Scenario: Canonical cutover is blocked

- **WHEN** 任意运行时或隐私 blocker 未关闭
- **THEN** authority 仍为 shadow/legacy，报告明确记录 FAIL 和下一步修复项
