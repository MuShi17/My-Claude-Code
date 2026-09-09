## 1. OpenSpec 与数据边界

- [x] 1.1 固定 `streaming-partial-persistence` 的 partial snapshot、immutable event、fallback 和 final cleanup 契约，并核对实现范围不扩展到 Maka 或 ArchiveRead
- [x] 1.2 将 SQLite schema 版本提升到 5，新增 `runtime_stream_partials` 表、索引、数据类和安全迁移；保留既有 `runtime_partial_snapshots` 表及 direct `append(partial)` 行为

## 2. Sink 与 SQLite 持久化

- [x] 2.1 为 `RuntimeEventEmitter`/`CanonicalSink` 增加可选 `emit_partial_batch`、原子 final cleanup 和 streaming snapshot 读取委托，旧 sink 回退到原有 `emit()`；明确 unsupported sentinel 与 `None` 成功返回的区别
- [x] 2.2 实现 `SQLiteRuntimeStore.append_runtime_partial_batch()`：首片锚定、同 stream 聚合、8 KiB batch 事务、identity/partial sequence 校验、幂等和 digest 校验；修正 run-root 与 model-call invocation 的边界
- [x] 2.3 实现 `SQLiteRuntimeStore.append_event_and_clear_runtime_partials()` 及读取/列举接口，使 final/error/budget 与 snapshot 删除在同一事务完成
- [x] 2.4 覆盖 SQLite partial batch、final cleanup、commit rollback、closed store、重复 batch、同序冲突、重开读取和 direct append 兼容测试
- [x] 2.5 拒绝缺少 `partial_seq` 的优化 batch，并补 stream identity 矩阵及单批次 SQLite transaction 计数回归

## 3. Recorder 与 Agent 生命周期

- [x] 3.1 让 `ModelCallRecorder` 为 text/thinking/tool arguments 派生隔离 stream key，首片同步写入，后续按 8 KiB 或 80 ms asyncio timer 缓冲并 flush
- [x] 3.2 将 final text/thinking/tool call、finish、provider error、budget 和 retry 接到 flush/cleanup 边界；失败时保留 buffer/snapshot，不提前标记 finished
- [x] 3.3 在 Agent turn finally 与 `aclose()` 中先 flush recorder partial，再 flush/close canonical sink；确认取消和异常路径不从 SQLite 连接创建后台线程写入
- [x] 3.4 增加 recorder/Agent 回归测试：timer、byte threshold、多流隔离、retry、final/error cleanup、取消/关闭和旧 RecordingEventSink fallback
- [x] 3.5 为 Anthropic thinking 和 OpenAI reasoning streaming 增加本地 consumer 回归，锁定 stream kind 与 canonical ledger 隔离
- [x] 3.6 清除成功恢复后的 timer flush error marker；在序号分配前消费 marker，确保失败调用不消耗序号，并覆盖 explicit flush 后继续 delta/retry 的回归

## 4. Recovery 与本地消费者

- [x] 4.1 让 RecoveryProjection 能发现 pending streaming snapshot，并以 bounded diagnostic 暴露，不将其重建为 model history、tool call 或 tool outcome
- [x] 4.2 通过实际 Agent 的本地 fake Anthropic/OpenAI stream 入口验证 partial 持久化、final cleanup、最终 provider request 和工具执行语义未改变
- [x] 4.3 验证新 SQLite 打开、resume/reopen、canonical replay、session snapshot 和现有历史 direct partial 数据的兼容性；旧表/旧行保持不变
- [x] 4.4 用不含新表的真实 v4 数据库夹具验证迁移、旧 context snapshot 和 canonical event 保留

## 5. 验证与知识库回写

- [x] 5.1 运行 streaming focused tests、`src/mini_claude/tests` 全量测试、compileall、`git diff --check` 和 OpenSpec strict validate，记录最终命令与边界
- [x] 5.2 对照 Maka 的源码重新核对 80 ms/8 KiB/partial snapshot/final ledger 语义；只记录可复现的本地证据，不宣称外部 Provider 或生产性能
- [x] 5.3 更新 `D:/software/obsidian/MuShiKnowlegde/04_Projects/my-coding-agent/work/2026-09-09-Maka流式增量与CanonicalSQLite落盘边界.md`，写入根因、变更文件、验证结果、残余风险和本 change 链接
- [x] 5.4 更新 `implementation-validation.md`，确认代码、OpenSpec、问题记录和测试结果一致；本轮不执行 commit、push、MR、merge、release 或 deployment
- [x] 5.5 在补充缺口后重新运行 focused/full/OpenSpec 验证，并记录独立 delta gap closure 结论

## 6. 旧 sink fallback 顺序修复

- [x] 6.1 让 `RuntimeEventEmitter` 暴露本次 recorder 是否使用 streaming batch 扩展；保持 unsupported sentinel 与扩展 sink `None` 成功返回语义不变
- [x] 6.2 为 recorder 保留 pending partial 的全局 arrival-order；旧 sink 在 `usage`、retry、普通生命周期和 final/terminal 边界前 flush 全部 pending，SQLite 优化路径继续使用 per-stream buffer 和 selective cleanup
- [x] 6.3 增加旧 sink 回归：partial 与 usage 交错、多 stream partial 与 `final_text` 交错，并验证 `CanonicalSink(RecordingEventSink())` fallback 顺序；重新运行 focused/full/OpenSpec 验证并回写问题记录
