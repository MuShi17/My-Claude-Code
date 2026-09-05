## Purpose

禁止在 Canonical Store 无法持久化终态时报告成功。

## ADDED Requirements

### Requirement: Finalize failure is controlled failure

Canonical finalization 的 append/seal/fsync/finalizer 失败 MUST 使当前 Run 进入可观测 controlled failure。

#### Scenario: Seal fails during chat cleanup

- **WHEN** chat finally 阶段 canonical seal 抛错
- **THEN** Agent/CLI 返回失败状态并保留诊断，不继续声称 run completed

### Requirement: Original errors remain visible

finalize failure MUST 不得覆盖更早的 provider/tool/cancellation error，且两者可关联。

#### Scenario: Provider and finalize both fail

- **WHEN** provider 先失败且 terminal finalize 随后失败
- **THEN** 原始 provider error 仍可见，并附带 canonical finalize diagnostic
