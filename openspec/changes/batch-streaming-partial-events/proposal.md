## Why

当前 `ModelCallRecorder.partial_text()` 和 `partial_tool_arguments()` 经由 `RuntimeEventEmitter` 对每个流式增量调用一次 SQLite `append()`。每个增量都会启动独立的 `BEGIN IMMEDIATE`/commit，导致高频磁盘 I/O；更重要的是，尚未完成的流式观察被当作 immutable `runtime_events` 写入，和最终语义事件的落盘边界混在一起。对照 Maka，应把流式增量作为可变 partial snapshot：首片同步建立恢复锚点，后续增量按大小或时间批量合并，最终/失败事件完成后再清理 snapshot。

## What Changes

- 为 SQLite 增加独立的 streaming partial snapshot 表和读接口；它不改变既有 `runtime_partial_snapshots` 上下文快照 API。
- 为 `RuntimeEventEmitter` 增加可选的 partial batch 与 final cleanup 边界；不支持新接口的旧 sink 使用全局 arrival-order pending 队列逐事件回退，并在普通/终态事件前先 flush，保持事件边界顺序。
- 让 `ModelCallRecorder` 对流式增量执行“首片同步、后续 8 KiB 或 80 ms 批量 flush”；文本/思考增量和每个工具调用参数流分别聚合，不跨 invocation/attempt/call 串流。
- 在最终模型事件、provider error、预算终止、retry 和 Agent turn cleanup 前完成 pending partial flush；最终 canonical event 与对应 snapshot 删除在 SQLite 同一事务内完成。
- 优化的 streaming partial batch 要求 recorder 提供显式 `partial_seq`；缺少序号时在开启事务前 fail-closed，避免无法区分新片段与历史重放而隐式重复聚合。
- 流式 partial 不进入 SQLite immutable `runtime_events`；直接调用既有 `SQLiteRuntimeStore.append(partial_event)` 的外部兼容行为保留。
- timer flush 失败后，显式重试成功必须清除已恢复的失败 marker，后续 delta/retry 不得重复抛出旧异常。
- timer 失败 marker 在分配下一个 `partial_seq` 前检查；被旧 marker 拒绝的调用不得消耗序号，恢复后下一片仍保持连续序列。
- 旧 sink fallback 不得让 pending partial 越过 `usage`、retry、final 或其他普通生命周期事件；扩展 SQLite sink 继续使用 per-stream buffer 和 selective cleanup。
- 增加事务、幂等、缺少序号、取消/关闭、重开恢复、批量 I/O 和双 Provider 流式路径回归测试，并更新问题记录与实施验证证据。

## Capabilities

### New Capabilities

- `streaming-partial-persistence`: 定义 provider 流式观察、可变 snapshot、批量提交、最终语义事件和故障恢复之间的持久化边界。

### Modified Capabilities

- 无。当前 `openspec/specs/` 没有可直接修改的同名主规格；本 change 新建独立 capability，避免扩大既有 Provider projection change 的验收范围。

## Impact

- 主要影响 `src/rollo/runtime_lifecycle.py`、`event_sink.py`、`runtime_store.py`、`agent.py` 和对应 Python 测试。
- SQLite schema 版本增加一个向后兼容的表迁移；既有 immutable event、tool operation、上下文快照和归档数据不迁移、不重写。
- 运行时仍使用单个 Agent/SQLite 连接，不引入线程写入或外部依赖；partial timer 绑定当前 asyncio event loop，无法获得 event loop 时通过显式 flush/最终边界完成持久化。
- 不修改 `D:/workspace/maka`，不改变 Provider wire message、工具权限、ArchiveRead、归档内容上限或 Git 交付状态。
