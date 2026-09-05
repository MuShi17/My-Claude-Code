## Purpose

用真实 CLI 入口证明 shadow/legacy 读取、恢复和回滚路径，而不是只证明独立模块。

## ADDED Requirements

### Requirement: CLI lifecycle is evidenced

临时 HOME 下 MUST 覆盖 one-shot、list/latest、resume、shadow 和 rollback，并使用 deterministic provider fixture。

#### Scenario: One-shot then resume

- **WHEN** CLI 写入一次 fake-provider one-shot 后执行 latest/resume
- **THEN** 输出、canonical/legacy 数据和 exit status 可验证且不需要网络

### Requirement: Acceptance tests are current

已实现能力 MUST 不保留误导性的 pending `xfail` 或固定历史测试数量断言。

#### Scenario: Full suite runs

- **WHEN** 使用 py313 执行完整和 `--runxfail` 测试
- **THEN** 没有因 stale xfail 或旧 passed 文案造成的假阳性
