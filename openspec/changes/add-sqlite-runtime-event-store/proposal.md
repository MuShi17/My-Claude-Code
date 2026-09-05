## Why

没有 durable event store，canonical event 只能停留在内存或 JSONL，无法提供 store-assigned ordering、exact replay、崩溃后高水位、partial snapshot 和唯一终态。需要用标准库 SQLite 建立单进程/单逻辑 writer 的可靠事实存储，作为后续 Agent Loop、投影和恢复的持久化基础。

## What Changes

- 增加 runtime event SQLite schema，保存 events、session ordinals、partial snapshots、run state 和可控 LLM captures。
- 实现 append、exact-ID idempotency、冲突检测、显式 ordinal、高水位读取和 run seal。
- 在事务边界内保证 dispatch/lifecycle 事件的顺序与可恢复性，并定义 typed store errors。
- 支持数据库初始化、迁移、关闭/重开和损坏/重复写诊断，不实现分布式复制。
- 为 C06～C10 提供稳定的 Store/EventSink 接口和故障测试入口。

## Capabilities

### New Capabilities

- `sqlite-runtime-event-store`: 基于 SQLite 的 canonical event durability、ordering、idempotency、partial snapshot、LLM capture 和 run sealing。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 新增 `src/mini_claude/runtime_store.py` 及 SQLite schema/migration 测试。
- 默认数据目录扩展为 `~/.mini-claude/runtime.sqlite`（实际路径由现有 session 配置解析），不删除 legacy 文件。
- 依赖 C01～C04 和 Python 标准库 `sqlite3`；不新增 ORM、migration framework 或外部服务。
