## Purpose

为 Canonical Runtime Event 提供事务化、可重开、可按高水位重放且具有明确封存语义的本地持久化，使 Agent Loop 不再依赖多个非原子 legacy 文件表达运行事实。

## ADDED Requirements

### Requirement: Store schema persists canonical runtime facts

本地 store MUST 持久化 canonical event envelope、store ordinal、session/turn/run/invocation 索引，以及 partial snapshot、run state 和受策略控制的 LLM capture metadata；schema 必须带可迁移版本。

#### Scenario: Database opens for a new user

- **WHEN** 首次打开 runtime store
- **THEN** 系统创建当前 schema 所需表、索引和版本记录，不要求手工执行 SQL

#### Scenario: Existing database reopens

- **WHEN** 已有数据库在进程重启或升级后打开
- **THEN** store 读取已有事件并执行兼容迁移或明确返回 migration error，不丢弃旧 canonical facts

### Requirement: Append assigns explicit order and supports exact replay

成功 append MUST 在事务中分配单调 store ordinal 并保留 event identity；完全相同 identity 与 payload 的重复 append MUST 幂等返回原结果，identity 相同但 payload 不同 MUST 被拒绝。

#### Scenario: Concurrent-looking same writer appends

- **WHEN** 单逻辑 writer 连续 append 两个 timestamp 相同的事件
- **THEN** 两个事件获得不同且递增的 ordinal，读取顺序不依赖 timestamp

#### Scenario: Exact replay is retried

- **WHEN** caller 因不确定返回重试完全相同的 event
- **THEN** store 不产生第二条事实或第二个 ordinal，并返回原 event 的 durable result

### Requirement: Store enforces run seal and terminal uniqueness

每个 run MUST 能被一个 terminal event 封存；terminal 成功提交后，store MUST 拒绝普通、partial、不同 terminal 和未经授权的更新，重复提交相同 terminal identity MUST 幂等。

#### Scenario: Event after terminal is rejected

- **WHEN** 已封存 run 收到新的模型、工具或 partial event
- **THEN** append 返回 typed sealed-run error，数据库内容和 terminal 不改变

#### Scenario: Conflicting terminal is rejected

- **WHEN** 同一 run 已为 `failed` 又提交不同 identity 的 `completed` terminal
- **THEN** store 拒绝冲突并保留首次成功 terminal

### Requirement: Partial snapshots are bounded and recoverable

store MUST 支持按 run 保存 bounded partial snapshot/high-water，用于崩溃恢复但不把 snapshot 当作 canonical event；snapshot 更新和读取必须可识别版本、覆盖范围和创建 ordinal。

#### Scenario: Streaming run crashes with partial snapshot

- **WHEN** 进程在 terminal 前退出且已有 partial snapshot
- **THEN** 重开 store 可读取该 snapshot 的 high-water/coverage，并由 recovery 层判断为 partial/open，而非伪造 completed

#### Scenario: Snapshot exceeds bound

- **WHEN** partial payload 超过配置上限
- **THEN** store 拒绝或截断为明确的 bounded representation，并保留 high-water，不写入无限增长内容

### Requirement: Reads expose immutable prefixes and high-water

读取 MUST 使用显式 ordinal，支持按 session/turn/run/filter 获取 immutable prefix、当前 high-water 和 terminal 状态；读取过程不得隐式改写事件。

#### Scenario: Projection reads a stable prefix

- **WHEN** projection 请求某 run 截止指定 high-water 的事件
- **THEN** 返回顺序稳定的 prefix 和对应 digest；之后追加的事件不会改变该 prefix

#### Scenario: Read encounters malformed row

- **WHEN** store 发现无法解码的 canonical row
- **THEN** 读取返回可定位的 corruption error 或明确跳过策略，不把损坏 row 静默当成合法消息

### Requirement: Durability and errors are observable

canonical append、terminal seal 和必要的 dispatch boundary MUST 在成功返回前完成事务提交；I/O、锁、schema、validation、sealed-run、idempotency conflict 和 corruption MUST 可区分，legacy diagnostic failure 不得伪装为 store success。

#### Scenario: Commit fails

- **WHEN** SQLite commit 或连接发生错误
- **THEN** caller 收到 durable failure，并不能获得“已写入”的成功结果；重开后可验证事务未产生半条 event

#### Scenario: Database is locked

- **WHEN** 违反单 writer 假设或数据库返回 locked
- **THEN** store 返回明确错误和重试/升级边界，不自动执行可能重复副作用的重试
