## Context

C04 提供经过校验和脱敏的 EventSink/domain event。当前项目只有 JSON 文件 session/日志，没有 SQLite runtime schema；批次明确只允许标准库 `sqlite3`，并把单进程/单逻辑 writer 作为边界。后续 C06 需要在工具执行前让 dispatch durable，C08～C10 需要 high-water 和重开读取。

## Goals / Non-Goals

**Goals:**

- 以事务化 SQLite 表提供 append/order/idempotency/terminal seal/partial/high-water。
- 以 schema version 和迁移入口支持进程重启与未来兼容。
- 对 corruption、locked、commit failure、sealed 和 conflict 提供 typed errors 与 fault hooks。

**Non-Goals:**

- 不支持多机器复制、跨进程协调、workspace authority 或 continuation。
- 不把 LLM 原始 wire body、任意大工具结果或 legacy 文件复制进 canonical 表；大内容由 C09 归档。
- 不让 store 自动重试有副作用的不确定操作。

## Decisions

1. **SQLite 表按事实与索引分离。** `runtime_events` 保存 canonical JSON、identity、ordinal、digest；`runtime_session_event_ordinals` 支持 session 高水位/查询；`runtime_partial_snapshots`、`run_state`、`llm_captures` 分别承载派生/受控辅助数据。这样能重放事实且避免把 projection 当真相。
2. **唯一键与事务双保险。** event identity/digest 通过唯一约束和事务内比较实现 exact replay；冲突显式报错。ordinal 由同一数据库连接的事务分配，不依赖时间。
3. **seal 与 append 在同一状态机。** terminal event 和 run_state seal 原子提交；所有未来 append 在事务内检查 seal。相比独立 session status 文件，可避免 crash 造成双权威。
4. **JSON canonical bytes + hash 存储。** domain 已完成类型校验/脱敏，store 保存 canonical bytes/digest 和必要索引；读取再严格 decode，遇损坏返回定位错误。
5. **显式 schema migration。** 数据库有 schema version 与小步迁移函数；失败不删除或覆盖数据库，要求人工/后续 change 处理，保留旧版本备份策略。

## Risks / Trade-offs

- [SQLite 单 writer 锁等待] → 明确单逻辑 writer、短事务和 typed locked error；不隐式扩展到并发模型。
- [事件已提交但 legacy shadow 未提交] → C04/C06 使用独立 diagnostic policy，C11 parity 报告 gap，不回滚 canonical。
- [schema 迁移破坏旧数据] → 临时副本/事务迁移、版本测试和重开验证；只增加兼容字段/索引。
- [partial snapshot 被误当事实] → 表和 API 明确 snapshot 非 event，recovery 只能将其分类为 partial/open。

## Migration Plan

新用户直接创建 runtime.sqlite；已有用户先只新增数据库并继续写 legacy shadow。C10 从 canonical 读取并对旧 session 做只读 fallback；C11 通过 shadow/rollback smoke 验证数据库失败不会删除或损坏旧文件。
