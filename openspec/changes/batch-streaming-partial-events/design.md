## Context

当前 provider 流式解析在每个文本、思考或工具参数增量到达时调用 `ModelCallRecorder`，而 recorder 通过 `RuntimeEventEmitter.emit()` 直接落到 SQLite `runtime_events`。SQLite store 的 `append()` 每次都执行独立的 `BEGIN IMMEDIATE` 和 commit，因此高频增量既造成不必要的磁盘事务，也把尚未完成的观察误当成 immutable canonical fact。

Maka 将这两类数据分开：首个增量同步建立可恢复的 partial snapshot，后续增量在同一 stream key 下内存合并，达到时间或大小阈值时批量写入；最终语义事件才进入 immutable ledger，并清除对应 snapshot。本项目已经有一个用于上下文快照的 `runtime_partial_snapshots` 表，语义不同，不能复用。当前直接调用 `SQLiteRuntimeStore.append(partial_event)` 的公共 API 也被已有恢复/测试夹具使用，必须保持兼容。

## Goals / Non-Goals

**Goals:**

- 为 recorder 驱动的 provider streaming partial 建立独立、可重开的 SQLite snapshot 边界。
- 首片同步持久化；同一 invocation/attempt/stream key 的后续增量按 8 KiB 或 80 ms 批量合并，整个 batch 使用一个 SQLite 事务。
- 文本、思考和各个工具调用参数流互相隔离；不把不同 invocation、attempt 或 call 的增量拼接在一起。
- final model event、provider error、预算终止和关闭路径在清理前保留已提交 partial；成功时将最终 canonical event 与 snapshot 删除放入同一 SQLite 事务。
- 保持 `runtime_events` 的 immutable replay、tool operation 和现有上下文快照 API 不变；直接 `append(partial_event)` 仍按旧契约工作。
- 在 timer 不可用、取消或进程重启时可通过显式 flush/读取 snapshot 发现未完成 partial，不静默丢失或伪造最终事实。

**Non-Goals:**

- 不改变 Provider wire message、最终响应解析、工具执行权限、ArchiveRead 或工具结果 16 MiB 序列化上限。
- 不把 partial snapshot 重新投影成模型历史消息，也不修改已有 immutable event/artifact。
- 不引入后台线程写 SQLite；不修改 `D:/workspace/maka`，不引入外部依赖。
- 不解决跨进程多 writer 协调、外部 Provider/部署性能或未经授权的 Git 交付。

## Decisions

### D1：在 recorder 与 sink 之间增加可选的 partial/final 边界

`RuntimeEventEmitter` 增加两个可选能力：

- `emit_partial_batch(events)`：先按现有 redaction/validation 准备事件，再把一个 partial batch 交给下游；没有该下游能力时逐个调用原有 `emit()`。
- `emit_final(event, clear_partial_stream_keys=(), clear_partial_invocation=False)`：准备并发出非 partial 事件；SQLite 下游可在插入 final event 的同一事务中删除指定 snapshot。没有原子能力的旧 sink 回退为普通 emit 加可选清理。

扩展方法的返回契约区分“下游不支持”和“下游已成功但没有返回值”。旧 sink 由 wrapper 返回内部的 unsupported sentinel；真正实现扩展的 sink 返回 `None` 时视为成功，不能再 fallback 重发，避免一次 partial 或 final 被写两次。

`ModelCallRecorder` 只对 `partial=True` 走内存缓冲；扩展 sink 使用 per-stream buffer，缺少扩展的旧 sink 使用全局 arrival-order 视图。旧 sink 的普通生命周期事件在有 pending partial 时必须先 flush，再通过原有 `emit()` 逐事件发送；因此该兼容路径会延迟普通事件，但不会改变 partial 与普通/终态边界的顺序。Agent 使用 SQLite 时仍自动获得新边界，直接 store API 不变。

备选方案是让 `SQLiteRuntimeStore.append()` 根据 `partial` 自动改变语义，但这会改变已有调用者对 `append(partial_event)` 的契约，并使通用 sink 无法保持一致，因此不采用。

### D2：新增 `runtime_stream_partials`，不复用上下文快照表

schema 版本从 4 增至 5，并新增一行对应一个 `stream_key` 的 `runtime_stream_partials`。行至少保存 session/run/invocation/attempt identity、stream kind、tool call identity、聚合后的安全 event payload、首尾 event id/timestamp、fragment count、last partial sequence、payload byte size、digest、created/updated 时间。

优化的 `append_runtime_partial_batch()` 要求每个输入都带显式正整数 `partial_seq`，缺少序号时在开启事务前 fail-closed；否则无法在新片段和历史重放之间建立可靠的幂等判断。store 对没有内部 metadata 的旧输入仍使用稳定回退 key，以便读取既有首片快照；既有 direct `append(partial)` 不受该要求影响。

`stream_key` 由 invocation、attempt、stream kind 和可选 tool call id 派生；recorder 在 metadata 中写入 `partial_stream_key` 与单调的 `partial_seq`。store 对没有内部 metadata 的旧输入使用稳定回退 key。payload 是最新 partial event 的 canonical envelope，但其 text/args 已按同一 stream 聚合，便于恢复诊断而不进入 model replay。`runtime_run_state.invocation_id` 是 run 的根 invocation，不等于每次 Provider model-call 的 invocation；partial 写入必须校验当前 run 的 session、未封存状态，以及该 event 自己已有 `invocation_opened`，不能错误要求两者相等。

`append_runtime_partial_batch()` 在一个 `BEGIN IMMEDIATE` 中校验同一批事件的 session/run/invocation 兼容性、当前 run 未封存状态和 invocation opening，并按 stream key upsert。已提交的 `partial_seq` 重复到达时视为幂等，不重复拼接；最新 sequence 还保存事件 digest，使同序不同 payload fail closed。读取时重新验证 JSON、digest、stream metadata 和 RuntimeEvent envelope，损坏数据返回 `CorruptionError`。

### D3：首片、批量和 stream-key 切换

新 stream key 的第一片先同步调用 `emit_partial_batch([event])`，确保 provider 继续流式时已有可恢复锚点。后续片只追加到 recorder 的内存 buffer；buffer 序列化字节达到 8 KiB 时立即 flush，或者在当前 asyncio loop 上安排 80 ms timer。recorder 同时保留 pending partial 的全局 arrival-order；旧 sink fallback 按该顺序逐事件发送，扩展 SQLite sink 仍按 stream key 选择要刷新的 buffer。新 stream key 出现时先 flush 尚未提交的旧 buffer，再建立新锚点，避免跨流拼接。

timer 只使用 event-loop `call_later`，不从线程触碰 SQLite。timer 失败不会吞掉异常：保留 buffer，并在下一次 recorder 操作或显式 flush 时向调用者报告；显式重试成功前不声称 partial 已持久化。没有 running loop 时不安排 timer，但最终边界、Agent cleanup 和显式 `flush_partials()` 仍保证落盘。

显式 `flush_partials()` 成功提交当前所有 pending buffer 后，必须清除对应的 timer flush error marker；否则一次已恢复的异常会在下一片或 retry 中被重复抛出。若只 flush 指定 stream，仍有其他 pending buffer 时不得清除全局失败 marker。

新的 partial 在分配 `partial_seq` 前必须先消费已有 flush error marker；因此被 marker 拒绝的 delta 不得推进内存序号，显式恢复后下一片继续使用下一个连续序号。

### D4：最终事件与 snapshot cleanup 的原子关系

`final_text`/`final_thinking` 清理当前 attempt 的对应 text stream，`final_tool_call` 清理对应 call stream；`finish`、provider error 和 budget terminal 清理当前 invocation 的所有 stream。recorder 在清理前先 flush pending buffer；SQLite 的 `append_event_and_clear_runtime_partials()` 再在一个事务中调用既有 `_append_in_transaction()` 并删除匹配 rows。

对于不支持 streaming 扩展的旧 sink，任何普通生命周期事件（包括 usage、retry 和非 terminal event）以及任何 final/terminal event 前都必须 flush 全部 pending partial；旧 sink 的 final cleanup 仍退回普通 `emit()`，不引入 snapshot 删除语义。SQLite 扩展路径继续允许 final text/tool call 只清理对应 stream，其他 stream 的 snapshot 保持 pending。

如果 final event 插入或 commit 失败，事务回滚，partial snapshot 保留，recorder 不进入 finished 状态；如果成功，immutable event 成为唯一最终事实，snapshot 不再存在。`retry` 只先 flush 旧 attempt，不删除旧 snapshot，新的 attempt 使用新的 stream key，直至最终/错误边界统一清理。

### D5：取消、关闭和恢复

Agent 的 turn `finally` 与 `aclose()` 在调用通用 emitter flush 前先调用 recorder 的 `flush_partials()`。正常 final/error 路径通常已清理 snapshot；取消或进程中断则最多留下最后一次成功 batch，下一进程可用 `read_runtime_stream_partials()` 读取它。RecoveryProjection 只把 pending snapshot 作为 bounded warning diagnostic，不把它伪造成 canonical model message，也不自动删除或重试工具副作用。

### D6：验证策略

新增测试覆盖首片同步、80 ms/8 KiB flush、文本/思考/多工具流隔离、retry、final/error/close cleanup、SQLite 单事务、partial sequence 幂等、故障回滚、重开读取和 Recovery diagnostic。保留已有 direct `append(partial)` 测试。

补充回归还要覆盖：缺少 `partial_seq` 的优化 batch fail-closed、stream identity 矩阵、每批恰好一个 SQLite transaction、Anthropic thinking/OpenAI reasoning consumer、无新表的真实 v4 schema 迁移，以及 SQLite error/finish/budget cleanup 的成功与回滚。旧 sink fallback 还必须覆盖 partial 与 usage 交错、多 stream partial 与 final_text 交错，并断言 sink 观察到的顺序。

同时通过实际 Agent 的 Anthropic/OpenAI 本地 fake stream 入口确认 text、thinking/reasoning 和 tool 参数都走新 recorder 路径；这只证明本地代码与 SQLite 边界，不宣称外部 Provider 或部署证据。

## Risks / Trade-offs

- [Risk] 80 ms timer 只能在当前 asyncio loop 中安全执行；没有 loop 时单个 partial 在最终边界前可能只存在内存。→ [Mitigation] 首片同步写入，Agent finally/aclose 和显式 API 强制 flush；禁止线程 timer。
- [Risk] partial snapshot 是可变诊断状态，不再具有 immutable event ordinal。→ [Mitigation] 它不参与 canonical replay/source digest；最终语义 event 仍使用既有 append/ordinal，snapshot 仅保存 identity、digest 和 bounded recovery payload。
- [Risk] commit 在网络/磁盘故障下可能处于不确定状态。→ [Mitigation] batch 只在事务成功后清空 buffer；sequence/identity 使重复 flush 不重复拼接，final event 与 cleanup 原子化，失败保留 snapshot 并向上抛出。
- [Risk] 旧 sink fallback 需要在普通事件前 flush pending partial，会把普通事件短暂留在内存中。→ [Mitigation] fallback 只保留 bounded partial buffer，按全局 arrival-order 逐事件发送；SQLiteRuntimeStore 继续走独立的 per-stream batch/atomic cleanup，测试覆盖 usage 和多 stream final 边界。
- [Risk] 聚合 payload 随长流增长。→ [Mitigation] 沿用 recorder 的单片上限和已有 Provider 最终结果限制；本 change 不擅自截断 partial，若后续需要独立 snapshot 上限另立 change。

## Migration Plan

1. 发布代码后首次打开旧 SQLite 时执行幂等 schema migration，创建 `runtime_stream_partials` 并将 user_version 设为 5；旧表和旧 event 行不变。
2. 新运行只通过 recorder partial path 写新表；已有 direct append partial 行继续可读，历史数据不迁移。
3. 若回滚代码，schema 5 表会被旧版本视为 newer schema；因此回滚前应使用同版本兼容代码完成运行，或保留数据库副本后再回滚。正常新版本升级不需要数据转换。
4. 任何迁移/批量/最终事务失败都保留原数据库，不自动清理未知 partial 文件或 event。

## Open Questions

- 无。当前实现的公开恢复接口和 fallback 行为已在本设计中固定；真实外部 Provider/部署性能不属于本 change 验收。
