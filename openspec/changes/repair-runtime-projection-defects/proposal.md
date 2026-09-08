## Why

真实会话 `a77d8ea0` 证明大结果归档和 `ArchiveRead` 存储链路可以完成，但运行时的事实层、Provider 投影和终端投影仍存在四类完整性缺陷：同一工具调用被写入两次、越界 `ArchiveRead` 被静默当作成功空页、终端把正文预览截断在 metadata 之后，以及多次 Provider invocation 覆盖 run 起始时间。相同问题还会在历史 resume 和子 Agent 汇总路径中表现为不可读的 `bounded_ref`。

本次修订把“P0 修复”和“真实本地消费者证据”写成可执行契约。重点不是提高全局工具结果阈值，而是让大结果在第一次 Provider 请求确实无法容纳时才转为“前缀预览 + 可调用 `ArchiveRead`”，并让真实本地消费者能够验证这条闭环。

## Scope and acceptance

本 change 本批次的 P0 范围固定为：

1. final tool-call 事实唯一性、等价幂等、冲突 fail-closed，以及旧 session resume 不重复执行。
2. `ArchiveRead` 的越界/EOF/limit 契约和结构化错误。
3. Anthropic 与 OpenAI-compatible 两个最终本地 SDK 调用边界的 Provider content：首请求完整物化、容量救援、stale placeholder、ArchiveRead page 和无 capability 安全降级。
4. 终端对 `bounded_ref`、`archive_page`、`archive_read_error` 的有界正文优先显示。
5. 多 invocation 的 run metrics 起点、首 token 和 terminal duration。
6. canonical facts、artifact bytes、metadata、ref、digest 的不可变性与 closed-store/integrity fail-closed。
7. 新工具结果归档的逻辑 artifact identity 与内容 `sha256` 分离；相同内容跨 session 可以复用 blob，但不能复用带 session 授权的 metadata/ref。
8. canonical tool-result serialization boundary 改为对规范化后的最终 JSON UTF-8 字节数执行公共 `16 MiB`（16,777,216 字节）上限；二进制先规范化为 Base64 envelope 后再计数。
9. artifact 内容、逻辑 metadata 和 runtime-store mirror 的发布失败必须回滚或明确进入 recovery-required，不能向 canonical event 或 Provider 投放不可读的成功 ref。

“真实本地消费者”至少包括两个层次：

- 最终 SDK 边界：通过本地 fake Anthropic/OpenAI SDK 或 loopback transport 接收实际 Agent 发送的最终 request，断言 wire content，而不是直接调用 projection helper。
- CLI 新进程：启动实际 `mini-claude-py`/`python -m mini_claude` 子进程，使用本地协议 stub，不访问外部 Provider，断言 stdout、session 持久化和 resume。

测试夹具、fake/stub、redacted capture 和临时 session 只能证明本地行为；不能写成外部 Provider、部署或 Git 交付证据。P1 的父子 Agent 完整 close barrier、跨进程 capability 传递和更广泛历史兼容继续列在任务清单中，但不阻塞本批次 P0 结果。

## What Changes

- 统一 Provider 响应和 durable tool boundary 的工具调用事件语义：只有 `partial=false` 且 `metadata.lifecycle=tool_call_final` 的事件才是 canonical function-call 事实；每个 `run_id + call_id` 只有一个等价事实，跨 model invocation 也复用同一 durable operation，冲突时记录 bounded `call_identity_conflict` 并 fail closed。
- 为 `ArchiveRead` 冻结文本 Unicode 字符/二进制 byte offset、`total_units`、合法 EOF、越界和 limit 错误；`offset > total_units` 不再返回成功空页，`offset == total_units` 保留合法空 EOF。
- 冻结 Provider archive projection 的本地预算公式：使用 `int(模型上下文窗口 * 70%)` 得到有效窗口，再换算为保守字节预算；只有完整安全结果通过对应 Provider 最终 wire message-list fit 检查时才首请求全文内联；否则返回前 N 字符/字节预览、真实 continuation offset、ref 和可实际调用的 `ArchiveRead` 指引；二进制 preview 即使以 base64 传输，continuation 仍按 byte 计；若连恢复 envelope 都无法放入则返回 bounded `capacity_exhausted`，不发布不可恢复成功 placeholder。终端 projection 不复用 Provider JSON 字符串。
- 让 terminal renderer 对已知归档结果采用 typed、正文优先、有界格式化，避免把转义 JSON metadata 当作唯一可读内容。
- 修复 run 级 metrics 的首次 invocation 起点、显式 first-token 事件和 terminal duration，并保留 invocation 级耗时。
- 增加 P0 reducer、Session/Model Replay、ArchiveRead、双 Provider 最终 SDK 边界、CLI 新进程、resume、terminal、metrics 和 immutability 回归测试；记录真实本地消费者证据，不保存 secrets/raw body。
- 修复工具结果的公共序列化边界：所有内置工具、MCP、子 Agent、特殊工具和 durable boundary 共享同一套“先规范化、再按最终 UTF-8 JSON 字节计数”的 16 MiB（16,777,216 字节）契约，并补充二进制膨胀、Unicode 和不可序列化值测试。
- 为工具结果归档增加 session/run/parent-lineage/event/call/tool/body-digest/rewrite-version 派生的逻辑 artifact identity；底层 content blob 可按 sha256 去重，但每个逻辑 ref 的 metadata 独立授权，并在 metadata/mirror 失败时撤销未完成发布。

## Capabilities

### New Capabilities

- `runtime-projection-integrity`：约束 canonical final tool-call 唯一性、ArchiveRead 范围错误、Provider/terminal 投影分离、run 级 metrics 和本地消费者验收。

### Modified Capabilities

<!-- 当前 openspec/specs/ 没有已同步的主规格；既有 Maka 对齐 change 的 capability 由本 change 通过完整性约束承接，不声明为已存在的主规格修改。 -->

## Impact

- 影响 `src/mini_claude/agent.py`、`runtime_lifecycle.py`、`artifact_archive.py`、`archive_capability.py`、`archive_projection.py`、`ui.py` 以及 `projections/` 下的 runtime、session、model replay、provider context 和 metrics 投影。
- 影响 `src/mini_claude/tests/` 的回归测试、最终本地 fake SDK/loopback consumer、CLI 子进程夹具和临时 session 输出。
- 不改变 `D:/workspace/maka`，不扩大普通子 Agent allowlist；将公共工具结果安全上限统一为 16 MiB（16,777,216）canonical JSON UTF-8 字节，但不把它当作 Provider 容量方案；不删除或重写历史 artifact/event，旧 `artifact:sha256:*` ref 保持只读兼容。
- 本批次不执行 commit、push、MR、merge、release、deployment 或真实外部 Provider 调用。
