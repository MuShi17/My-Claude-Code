## Purpose

让 canonical 恢复对损坏数据保守、可诊断且不覆盖原始证据。

## ADDED Requirements

### Requirement: Corruption is not missing

Canonical store 打开或完整性检查失败时 MUST 分类为 corruption 并保留原数据库，不得静默当作无 canonical 数据。

#### Scenario: Runtime database is corrupt

- **WHEN** resume 打开 runtime.sqlite 发生 integrity/schema 错误
- **THEN** CLI 给出 corruption 诊断并停止 canonical resume，不自动切换 legacy 继续运行

### Requirement: Runtime stores are session-isolated

新建和恢复的 session MUST 使用统一、可预测且相互隔离的 runtime.sqlite 路径。

#### Scenario: Two sessions resume

- **WHEN** 两个 session 分别写入 runtime events
- **THEN** 读取一个 session 不会混入另一个 session 的 canonical events
