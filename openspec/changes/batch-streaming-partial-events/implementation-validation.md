# Streaming partial 与 Canonical SQLite 实施验证

## 任务与边界

- canonical task source：`openspec/changes/batch-streaming-partial-events/`
- 用户授权：核实并修复 `2026-09-09-Maka流式增量与CanonicalSQLite落盘边界.md` 中记录的流式增量逐条落盘问题，以及完成后发现的旧 sink fallback 事件顺序问题。
- 写入者：主 Agent；本轮不执行 commit、push、MR、merge、release 或 deployment。
- 参考实现：只读对照 `D:/workspace/maka`；不修改 Maka。
- 本轮范围：ModelCallRecorder partial snapshot、SQLite batch transaction、final cleanup、Agent cleanup、Recovery diagnostic、本地 Anthropic/OpenAI consumer 验证。
- 非本轮范围：Provider wire 格式改变、工具结果归档/ArchiveRead、真正 range read、跨进程多 writer 协调、外部 Provider 或生产性能结论。

## 根因核实

原实现的调用链为：

```text
provider delta
  -> ModelCallRecorder.partial_text/partial_tool_arguments
  -> RuntimeEventEmitter.emit
  -> SQLiteRuntimeStore.append
  -> runtime_events 每个 delta 一个 BEGIN IMMEDIATE/commit
```

这会将未完成的展示观察写入 immutable ledger，并将 delta 频率直接转换为 SQLite transaction 频率。

实现第一版在本地真实 Agent consumer 验证中又暴露出一个边界错误：`RunStateGuard` 保存的是 run 根 invocation，而每次 Provider model-call 使用独立的 request id 作为 invocation。partial store 若要求两者相等，会拒绝所有真实 Agent 的首个 partial。当前修复改为校验：run 的 session/未封存状态、以及该 model-call invocation 自己已有 `invocation_opened` event；不再错误比较 run-root invocation。

随后对旧 sink fallback 做最小复现发现：recorder 的 per-stream buffer 在 `usage` 等普通事件前没有全量 flush，且 `final_text` 只 flush 当前 text stream；`RuntimeEventEmitter` 对缺少 streaming 扩展的 sink 又逐事件 generic emit，导致 pending partial 越过普通/终态事件。该缺陷只影响旧 sink 的可观察事件序列，不改变 SQLite 优化路径的 snapshot/ledger 分离。

## 实施摘要

1. SQLite schema 从 4 升到 5，新增 `runtime_stream_partials` mutable snapshot 表；原 `runtime_partial_snapshots` 和 direct `append(partial)` 契约不变。
2. `ModelCallRecorder` 对 text、thinking、每个 tool-call argument stream 使用独立 key；首片同步写 snapshot，后续按 8 KiB 或 80 ms 在当前 asyncio loop 批量 flush。
3. SQLite partial batch 在一个 `BEGIN IMMEDIATE` 中 upsert；校验批次 session/run/invocation 兼容、run 未封存、model-call opening、payload digest 和 stream metadata。已提交最新 sequence 重放不重复拼接，同序不同 event identity/digest fail closed。
4. final text/thinking/tool-call、finish、provider error、budget 与 snapshot cleanup 通过原子 final boundary；SQLite 中 final immutable event 与对应 snapshot 删除同一事务完成。失败时事务回滚、snapshot 保留、recorder 不提前标记 finished。
5. Agent turn `finally` 与 `aclose()` 先 flush recorder partial，再 flush/close canonical sink；RecoveryProjection 只报告 pending partial diagnostic，不把 snapshot 伪造成 model history、tool call 或 tool outcome。
6. Streaming sink extension 明确区分 unsupported sentinel 与“已成功但返回 None”，避免成功后又通过 generic `emit()` 重发。
7. 旧 sink fallback 记录 batch 扩展能力，并按全局 partial arrival-order 在普通/终态事件前 flush；SQLite 扩展路径继续保留 per-stream buffer 和 selective cleanup。

## 修改文件

补充边界：优化 batch 对缺少正整数 `partial_seq` 的输入在开启 SQLite 事务前 fail-closed；旧 sequence-less 读取和直接 `append(partial)` 兼容路径不受影响。timer flush 成功恢复全部 pending buffer 后清除失败 marker，并在分配下一个 `partial_seq` 前检查 marker，避免已恢复错误重复抛出或失败调用制造序号 gap。旧 sink fallback 维护 pending partial 的全局 arrival-order，并在 `usage`、retry、普通生命周期及 final/terminal 边界前完成全量 flush；SQLite 优化路径仍按 stream key selective flush/cleanup。

- `src/rollo/runtime_store.py`
- `src/rollo/runtime_lifecycle.py`
- `src/rollo/event_sink.py`
- `src/rollo/agent.py`
- `src/rollo/recovery.py`
- `src/rollo/tests/test_streaming_partial_persistence.py`
- `src/rollo/tests/test_local_consumers.py`

## 独立审查与主 Agent 判断

- `explore` 子代理确认 Maka 的目标方向正确，并静态定位了 run-root/model-call invocation 冲突；该意见已通过本地 Anthropic/OpenAI 测试复现后采纳。
- `test-strategy-agent` 初审指出 sequence 冲突、budget terminal 状态、扩展方法 `None` 返回语义、Recovery 隔离和本地 consumer 证据缺口。主 Agent 只采纳其中有明确代码/契约依据的项目：修复 model-call invocation 校验、增加最新 sequence digest 冲突检查、延后 `budget_exceeded` 的 `_finished` 状态、固定 optional extension 的返回语义并补回归断言；没有把“partial snapshot 总大小上限”等非本问题范围建议擅自扩大为本次实现。
- 子代理在实现过程中观察到工作树未冻结，因此其初审结果不是最终验收结论。最终验收以当前冻结差异、独立测试结果和主 Agent 复核为准。
- 实现完成后的独立 delta 审查发现并复现了 G-08（sequence-less 优化 batch 会产生不可读聚合），并列出 G-09～G-12 的回归缺口；主 Agent 未无条件采纳非本 change 的总大小上限建议，仅采纳了有代码证据的 G-08 和相关测试缺口。G-08 已修复，G-09～G-12 已补充对应回归；最终结论仍以修复后的本地验证和主 Agent 冻结前复核为准，不宣称真实 Provider/生产验收。
- 同一独立审查随后发现 G-13：timer 失败 marker 在显式恢复后未清除，且被 marker 拒绝的 delta 会消耗序号。主 Agent 通过最小 SQLite 故障注入复现后修复了 marker 清理和序号分配顺序，并新增选择性 flush、继续 delta/retry 回归；该审查结论是在修复前产生，修复后的最终状态已重新冻结并由后续独立 delta 审查复核。
- 修复 G-13 后的最终冻结 delta 审查返回 `sufficient`，G-01～G-13 全部闭合，未发现当前 accepted contract 下可复现的 P0/P1；审查明确未将 partial 总大小上限、跨进程 writer、真实 Provider/生产性能列为本 change 阻断项。
- 完成后主 Agent 对旧 sink fallback 进行最小复现，确认 `usage` 交错和多 stream `final_text` 交错均可重现；本轮仅修复该明确的 P2 顺序缺陷，并补充通过 `CanonicalSink(RecordingEventSink())` 的回归，没有扩大到无关的 SQLite snapshot 上限或真实 Provider 性能。

## 验证记录

以下结果在实现完成后更新；命令均在 `D:\workspace\My-Claude-Code`、PowerShell、UTF-8 环境执行。

| 命令 | 结果 | 证明范围 |
| --- | --- | --- |
| `python -B -m pytest src\\rollo\\tests\\test_streaming_partial_persistence.py -q --disable-warnings --tb=short` | 25 passed | snapshot、batch、timer、故障回滚、sequence conflict、budget、fallback、迁移、旧 sink 顺序 |
| `python -B -m pytest src\\rollo\\tests\\test_local_consumers.py -q --disable-warnings --tb=short` | 23 passed | 真实本地 Agent + Anthropic/OpenAI SDK transport、thinking/reasoning、final cleanup、Provider request 语义 |
| `python -B -m pytest src\\rollo\\tests\\test_streaming_partial_persistence.py src\\rollo\\tests\\test_local_consumers.py src\\rollo\\tests\\test_runtime_lifecycle.py src\\rollo\\tests\\test_runtime_store.py src\\rollo\\tests\\test_recovery_resume.py src\\rollo\\tests\\test_projections.py src\\rollo\\tests\\test_incremental_replay.py -q --disable-warnings --tb=short` | 93 passed | 本 change 专项、旧 sink 顺序、真实本地 consumer 与既有 runtime/projection focused 回归 |
| `python -B -m pytest src\\rollo\\tests -q --tb=short` | 330 passed，1 个既有 Proactor transport warning | Python 包全量测试；不等同于根目录全仓库测试 |
| `python -m compileall -q src\\rollo` | passed | Python 编译检查 |
| `openspec validate batch-streaming-partial-events --type change --strict --no-interactive` | passed | OpenSpec 工件结构/一致性 |
| `git diff --check` | passed | 差异空白；Git 仅提示 LF/CRLF 转换 |

已完成的证据：streaming 专项测试 25 passed；local consumers 23 passed；联合 focused/runtime 测试 93 passed；`src/rollo/tests` 全量 330 passed。此前在 invocation 校验修正前，local consumers 出现 15 个失败；修正后 local consumers 23 passed，说明该阻断点已被真实本地调用链复现并修复。focused 测试曾出现一次既有 Windows CLI subprocess/resume 的 `WinError 5` 原子替换瞬态失败，单独重跑和随后完整 focused 运行均通过。全量测试中的 1 个 Proactor transport warning 出现在既有 `test_compaction_artifacts.py`，不是本 change 新增失败。旧 sink fallback 的两个交错顺序回归已通过；最终独立 delta 审查判定 `sufficient`，既有 G-01～G-13 无剩余 gap。

## 证据边界与残余风险

- 本地 fake SDK/HTTP transport 证明的是本仓库 Agent 到 SQLite 的调用链，不是真实 Provider、部署、网关或生产磁盘 I/O 证据。
- partial snapshot 是 mutable recovery/presentation state，不加入 `read_events`、Model Replay 或 Provider Context；正常终态后应被删除，取消/进程中断可保留最后一次成功快照。
- SQLite commit 若处于真正的外部不确定状态，当前实现选择保守 fail-closed；不会为了“恢复成功”自动追加可能重复的 partial。
- 聚合 snapshot 随长流增长，当前沿用既有单片边界，不在本 change 新增独立总大小截断；如需总量保护应另立变更并定义恢复语义。
- OpenSpec 与问题记录只记录可复现的本地结构和测试，不把原笔记中的 `14,215` 或 `disk I/O error` benchmark 因果链表述为本轮独立复现结论。
