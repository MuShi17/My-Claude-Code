## Why

真实会话 `a77d8ea0` 证明大结果归档和 `ArchiveRead` 存储链路可以完成，但运行时的事实层、Provider 投影和终端投影仍存在四类完整性缺陷：同一工具调用被写入两次、越界 `ArchiveRead` 被静默当作成功空页、终端把正文预览截断在 metadata 之后，以及多次 Provider invocation 覆盖 run 起始时间。相同问题还会在历史 resume 和子 Agent 汇总路径中表现为不可读的 `bounded_ref`。

本次修订把“P0 修复”和“真实本地消费者证据”写成可执行契约。重点不是提高全局工具结果阈值，而是让大结果在第一次 Provider 请求确实无法容纳时才转为“前缀预览 + 可调用 `ArchiveRead`”，并让真实本地消费者能够验证这条闭环。

后续对当前实现的跨轮次复核又确认了两个未闭合缺陷：历史 canonical raw tool result 在新的 `Agent.chat()` run 中重新投影时，archive logical identity 包含当前 `run_id`，会为同一结果生成不同 logical ref；历史 placeholder 缺少 `read_instructions` 时则固定从 offset `0` 生成指引，可能丢失已有分页的 `next_offset`。这两个问题分别破坏跨轮次 Provider 前缀缓存和 ArchiveRead 续读语义。

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

本次推进额外纳入 Provider projection 的 P1 完整性边界：

10. stale/历史 `bounded_ref` 一旦建立 Provider 可见 placeholder，后续追加消息不得仅因为整体预算变化而把它改写为 `capacity_exhausted`；其 ArchiveRead 指引和 ref 必须保持稳定。
11. Provider 容量判断拆为“候选结果 fit”和 projection 完成后的最终 request gate。最终 gate 必须覆盖 Provider system、messages、tools 和既有输出预留；无法容纳时不得向 SDK 发送超预算请求。
12. 同一 session 中，相同 canonical event/tool call/body 在不同 `chat()` run 的历史投影复用同一 logical archive ref 和 metadata；当前 run 只参与授权/诊断，不参与 ref identity；不同 session 仍保持隔离。归档时若 tool message 缺少 `name`，应优先从同一 canonical assistant `tool_calls` 按 `tool_call_id` 回退解析工具名，避免 replay 形状差异改变 ref。
13. 历史 bounded placeholder 缺少 `read_instructions` 时，按 `next_offset → offset → 0` 恢复 continuation offset，并在值有效时保留已有 `limit`。

“真实本地消费者”至少包括两个层次：

- 最终 SDK 边界：通过本地 fake Anthropic/OpenAI SDK 或 loopback transport 接收实际 Agent 发送的最终 request，断言 wire content，而不是直接调用 projection helper。
- CLI 新进程：启动实际 `rollo`/`python -m rollo` 子进程，使用本地协议 stub，不访问外部 Provider，断言 stdout、session 持久化和 resume。

测试夹具、fake/stub、redacted capture 和临时 session 只能证明本地行为；不能写成外部 Provider、部署或 Git 交付证据。P1 的父子 Agent 完整 close barrier、跨进程 capability 传递和更广泛历史兼容继续列在任务清单中；本次只推进 Provider projection 的单调性和最终容量 gate。

## What Changes

- 统一 Provider 响应和 durable tool boundary 的工具调用事件语义：只有 `partial=false` 且 `metadata.lifecycle=tool_call_final` 的事件才是 canonical function-call 事实；每个 `run_id + call_id` 只有一个等价事实，跨 model invocation 也复用同一 durable operation，冲突时记录 bounded `call_identity_conflict` 并 fail closed。
- 为 `ArchiveRead` 冻结文本 Unicode 字符/二进制 byte offset、`total_units`、合法 EOF、越界和 limit 错误；`offset > total_units` 不再返回成功空页，`offset == total_units` 保留合法空 EOF。
- 冻结 Provider archive projection 的本地预算公式：使用 `int(模型上下文窗口 * 70%)` 得到有效窗口，再换算为保守字节预算；只有完整安全结果通过对应 Provider 最终 wire message-list fit 检查时才首请求全文内联；否则返回前 N 字符/字节预览、真实 continuation offset、ref 和可实际调用的 `ArchiveRead` 指引；二进制 preview 即使以 base64 传输，continuation 仍按 byte 计；若连恢复 envelope 都无法放入则返回 bounded `capacity_exhausted`，不发布不可恢复成功 placeholder。终端 projection 不复用 Provider JSON 字符串。
- 让 terminal renderer 对已知归档结果采用 typed、正文优先、有界格式化，避免把转义 JSON metadata 当作唯一可读内容。
- 修复 run 级 metrics 的首次 invocation 起点、显式 first-token 事件和 terminal duration，并保留 invocation 级耗时。
- 增加 P0 reducer、Session/Model Replay、ArchiveRead、双 Provider 最终 SDK 边界、CLI 新进程、resume、terminal、metrics 和 immutability 回归测试；记录真实本地消费者证据，不保存 secrets/raw body。
- 修复工具结果的公共序列化边界：所有内置工具、MCP、子 Agent、特殊工具和 durable boundary 共享同一套“先规范化、再按最终 UTF-8 JSON 字节计数”的 16 MiB（16,777,216 字节）契约，并补充二进制膨胀、Unicode 和不可序列化值测试。
- 为工具结果归档增加跨 chat run 稳定的逻辑 artifact identity：由 session、稳定的 runtime event key、tool call、规范化解析后的 tool name、发布后 body digest 和 rewrite version 派生，不把当前 run/parent run 纳入 ref；tool message 缺少 `name` 时从同一 assistant `tool_calls` 按 call id 回退解析；底层 content blob 可按 sha256 去重，run/parent lineage 继续用于授权和诊断，并在 metadata/mirror 失败时撤销未完成发布。
- 将 stale archive placeholder 与整体 Provider capacity 解耦：已有 placeholder 经过授权校验后保持稳定，不因后续 suffix 增长而改写为 `capacity_exhausted`；候选 preview/error 的 fit 仍由最终 Provider context envelope 判定。
- 在 Provider projection 完成后增加独立 final capacity gate，按 Provider 实际可见的 system/messages/tools 计算上下文字节；gate 失败时阻止 SDK dispatch，并保留 canonical/history 的原始事实和稳定 placeholder。
- 修复历史 placeholder 的 continuation hint：缺少指引时优先使用已有 `next_offset`，其次使用 `offset`，最后回退到 `0`，并保留合法的既有 `limit`。

## Capabilities

### New Capabilities

- `runtime-projection-integrity`：约束 canonical final tool-call 唯一性、ArchiveRead 范围错误、Provider/terminal 投影分离、run 级 metrics 和本地消费者验收。

### Modified Capabilities

<!-- 当前 openspec/specs/ 没有已同步的主规格；既有 Maka 对齐 change 的 capability 由本 change 通过完整性约束承接，不声明为已存在的主规格修改。 -->

## Impact

- 影响 `src/rollo/agent.py`、`runtime_lifecycle.py`、`artifact_archive.py`、`archive_capability.py`、`archive_projection.py`、`ui.py` 以及 `projections/` 下的 runtime、session、model replay、provider context 和 metrics 投影；本次增量重点落在 `archive_capability.py`、`archive_projection.py` 及其定向测试。
- 影响 `src/rollo/tests/` 的回归测试、最终本地 fake SDK/loopback consumer、CLI 子进程夹具和临时 session 输出。
- 不改变 `D:/workspace/maka`，不扩大普通子 Agent allowlist；将公共工具结果安全上限统一为 16 MiB（16,777,216）canonical JSON UTF-8 字节，但不把它当作 Provider 容量方案；不删除或重写历史 artifact/event，旧 `artifact:sha256:*` ref 保持只读兼容。
- 本批次不执行 commit、push、MR、merge、release、deployment 或真实外部 Provider 调用。

## Execution routing

- `issue_gate=reuse`：沿用本 change 作为唯一 OpenSpec 任务源；不新建竞争性 change。
- `profile=high-risk`、`bugfix_path=compact-high-risk-bugfix`、`execution_mode=A`：由主 Agent 作为唯一写入者实施，保留独立 Test Strategy 和实现后 Gap Closure。
- `writer_owner=main-agent`；允许修改范围为 archive logical identity/历史授权兼容、ArchiveRead continuation hint、Provider projection 相关定向测试和本 change 工件；禁止修改 Maka、artifact content 格式、普通 child allowlist、无关压缩策略和 Git 交付状态。

```yaml
artifact_plan:
  schema_version: 1
  live_task_ledger: required
  conceptual_model: omitted
  openspec: required
  execution_package: omitted
  test_strategy: required
  independent_review: inline
  project_work: omitted
  stable_knowledge: omitted
  additional_artifacts:
    - artifact_type: implementation_validation
      state: required
      reason: Provider projection and final local consumer evidence have an independent acceptance boundary
  reasons:
    openspec: Existing change already owns the canonical/provider projection contract
    test_strategy: High-risk runtime behavior requires an independent pre-check and post-implementation gap closure
```
