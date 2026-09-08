## Context

本 change 是 `repair-maka-aligned-archive-read-projection` 之后的完整性修复。当前运行时已经具备 SQLite canonical event、`ArtifactArchive`、`ToolResultArchiveCapability`、`ArchiveRead`、Provider request projection 和 terminal projection，但真实会话 `a77d8ea0` 暴露了四个边界不一致：

- Provider 响应路径和 `DurableToolBoundary` 都写入同一个 `function_call`/`tool_call_final`，19 个实际操作形成 38 个 assistant tool-call entry。
- `ArchiveRead` 只做切片，`offset > total_units` 时得到成功空页，不能区分越界和合法 EOF。
- terminal renderer 对序列化结果统一限制 500 字符，归档 preview 位于 metadata 之后；`ArchiveRead` JSON 还以转义字符串显示。
- metrics projection 在每次 `invocation_opened` 时覆盖 run 起始时间，并把任意 partial event 当作 first token，造成负的 `first_token_ms` 或不完整的 run duration。

既有 Maka 对齐 change 已定义首次 Provider 请求、capacity rescue、stale placeholder、scoped `ArchiveRead` 和 parent/child capability 的基本语义。本 change 冻结完整性边界，不重做 capability，不修改 `D:/workspace/maka`，也不把终端显示文本当成 Provider 输入。

## Goals / Non-Goals

**Goals:**

- 对每个 `run_id + call_id` 建立唯一、稳定、可重建的 canonical **final** function-call 事实，并保持完整工具生命周期和一次执行语义。
- 让 `ArchiveRead` 对字符/字节 offset、limit、EOF 和越界请求给出确定且有界的语义。
- 让 Provider 在首请求能容纳时使用完整安全结果；只有最终 request envelope fit 失败时才使用前缀 preview + ref + 可调用 `ArchiveRead`；stale placeholder 不因后续 suffix 或整体预算变化而反复变形，且无 capability 时不制造不可恢复成功态。
- 让 canonical tool-result boundary 在所有入口统一先完成值规范化，再按最终 canonical JSON 的 UTF-8 字节数执行公共 16 MiB（16,777,216 字节）上限；不让原始 bytes 的字符替换计数绕过 Base64/JSON 膨胀。
- 让新工具结果归档的逻辑 identity 与内容完整性 digest 分离，支持相同 blob 的安全去重而不复用跨 session 的授权 metadata。
- 让 artifact 发布在本地内容、逻辑 metadata 和 runtime-store mirror 任一阶段失败时不返回成功 ref，并清理本次创建的文件或留下明确 recovery-required 诊断。
- 让 terminal 对 bounded reference、ArchiveRead page 和 ArchiveRead error 单独做正文优先的有界格式化。
- 修复 run/invocation metrics 的时间边界，并增加多 invocation 单调性不变量。
- 以最终本地 SDK 调用边界和实际 CLI 新进程验证 Provider/terminal/resume 行为；把外部 Provider、部署和 Git 交付明确排除。

**Non-Goals:**

- 不改变 Maka 参考项目，不引入外部依赖或新的持久化协议。
- 不把所有归档结果全文内联，不把统一为 16 MiB 的公共结果上限当作规避 Provider 投影预算的手段。
- 不删除、重写或迁移历史 canonical event/artifact，不清理无关 legacy 日志。
- 不把 `ArchiveRead` 注入普通 child allowlist，不扩大 Agent 权限或递归能力。
- 不把旧内容哈希 ref 重新解释为新的 session-scoped identity；旧 ref 只按其已有 metadata 做兼容读取。
- 不修复与本 change 无关的 Windows asyncio transport warning、MCP、记忆、前端或部署问题。
- 不执行 commit、push、MR、merge、release、deployment，也不调用真实外部 Provider。

## Decisions

### D1：final tool-call 的身份、owner 和幂等边界

`function_call` 的 canonical 事实必须满足以下精确定义：

- `partial=true` 的流式工具名/参数增量只是观察事件，永远不进入 canonical call identity，也不触发工具执行计数。
- 只有 `partial=false` 且 `metadata.lifecycle == "tool_call_final"` 的 `content.kind == "function_call"` 才是 final call fact。
- 身份键为 `(run_id, call_id)`。payload signature 为 `name` 加上使用现有 `decode_tool_arguments` 解码后的 arguments 的稳定 canonical JSON（排序 key、紧凑分隔符、UTF-8）；字符串参数和 mapping 参数必须得到同一 signature。解码失败不得猜测或覆盖，记录 bounded conflict/invalid diagnostic 并 fail closed。
- Provider response recorder 是通常的 final-call semantic owner；`DurableToolBoundary` 只在非 Provider 入口确实缺失 final fact 时调用统一 `ensure_final_call`。任何入口再次提交同一等价 signature 都是幂等 no-op；同身份不同 name/arguments 必须记录 `call_identity_conflict`，不能静默替换。
- permission、dispatch、tool outcome、function response 和 Provider response 仍可各自产生生命周期事件，但不得另发第二个 final function-call fact。canonical writer 和 reducer 使用同一 identity helper，避免“写入去重”和“读取去重”规则分叉。
- 工具执行副作用由 durable operation journal 的 `(run_id, call_id)` 读取身份约束；operation record 保留原 invocation 归属以兼容历史 store，但同一 run 内后续 invocation 必须复用首条已完成 operation，等价重复 final event 不得重新执行，冲突或状态不确定时 fail closed。
- 历史读取发现冲突 identity 后，`SessionProjection`、`ModelReplayProjection`、incremental replay 和 Provider adapter 均不得生成该 call 的可执行消息；保留 bounded `call_identity_conflict` error diagnostic，直到显式修复冲突。

### D2：ArchiveRead 的单位、范围和响应

Archive 完整性校验通过后才计算 `total_units`：文本以解码后的 Unicode 字符计数，二进制以 byte 计数。请求校验顺序和结果固定为：

- `offset` 不是 `int`、是 `bool` 或小于 0：`invalid_range`。
- `limit` 不是正 `int`、是 `bool` 或超过 capability 上限：分别返回 `invalid_range` 或 `limit_exceeded`；这类请求不能产生 page。
- `offset > total_units`：`invalid_range`，不能通过 Python slice 得到成功空页。
- `offset == total_units`：合法 EOF，返回空 `archive_page`，`next_offset=total_units`、`has_more=false`。
- `0 <= offset < total_units`：返回不超过 limit 的 page；`next_offset = offset + 实际返回的单位数`，不得用请求 limit 猜测。

错误 envelope 只允许稳定 code/type、bounded message、必要的 ref/lineage 字段，不含本地绝对路径、traceback、secret 或另一 artifact 的内容。成功 page 必须保留 ref、digest、size、unit、offset、next_offset、total_units 和 has_more。

### D3：Provider projection 的预算和恢复

Provider projection 的预算是“本地投影预算”，不是 Provider SDK 的 token 计费或上下文保证。当前实现冻结为：

```text
effective_window = int(model_context_window * 0.70)
budget_bytes = max(0, int(effective_window) * 4)
fits_request(provider, system, tools, messages)
  := len(canonical_json_bytes(provider_request_context)) <= budget_bytes
```

`provider_request_context` 至少包含该 Provider 实际发送的 system（Anthropic 为独立字段）、messages 和 provider-specific tools；OpenAI 的 system 已位于 messages 中，不能重复计算。`effective_window` 仍预留模型上下文窗口的 30% 给模型输出和未建模开销；`*4` 仍是本地 token-to-byte envelope 的保守估算，不是 tokenizer 换算或 Provider API 保证。

候选 full/preview 的 `fits_request` 必须在替换目标 tool content 后，对经当前 Provider adapter 转换并加入 system/tools 的完整 context envelope 计算；不能只比较 artifact bytes、预览长度、中性消息列表或 terminal 文本长度。两个 adapter 共享 archive decision，但各自以最终 wire message shape 和 tool schema 做 fit。

结果状态固定如下：

1. 首次使用且完整 safe result 通过 `fits`：内联完整结果。
2. 首次使用但完整结果不通过 `fits`：按 artifact 的 unit 取前缀 preview（文本为前 N Unicode 字符，二进制为前 N bytes；二进制在 Provider wire 中可用 bounded base64 表示，但 continuation 仍按 byte 计），设置 `truncated=true`，`next_offset` 等于实际 preview 单位数，并附带 ref、metadata 和可实际调用的 `ArchiveRead` 指引。preview 预算使用现有 4,000 单位上限和递减 rescue，直到最终 Provider message envelope fit；不保证固定 N 一定能 fit。
3. 非首次/stale：不重新内联全文，保留经过授权校验的稳定 placeholder、ref 和 ArchiveRead 指引；不因当前完整 messages 的剩余 budget 不足而改写为 `capacity_exhausted`。
4. 当前 Agent 没有 scoped `ToolResultArchiveCapability`：不能发布只靠 `ArchiveRead` 才能恢复的成功 placeholder；只能保留已安全内联的结果或返回 bounded non-recoverable diagnostic。

projection 完成后必须再执行一次独立 final capacity gate。若完整 request context 仍超限，gate 必须阻止 SDK dispatch，并返回 bounded capacity verdict；不能把多个历史 placeholder 改成错误消息后继续发送。候选 `capacity_exhausted` 只有在替换后的完整 request context 自身 fit 时才可作为 Provider tool content 返回；否则不得构造一个仍超预算的错误 envelope。Provider projection 不读取 terminal renderer 的字符串，也不修改 canonical facts/artifact。

### D11：stale placeholder 单调性与容量职责分离

`bounded_ref` 是一个已建立的 Provider 可见状态，而不是每轮重新参与 aggregate capacity 竞争的原始结果。对非 first-use 的合法 ref，projection 只做 capability/session/scope/integrity 校验，并返回同一稳定 placeholder；如果历史形状缺少 `read_instructions`，仅按 ref/默认 limit 补齐一次可行动指引。该路径不得调用依赖完整 messages 的 `_fits()`，不得因为追加用户消息、记忆后缀或其他历史内容而产生 `capacity_exhausted`。

`_fits()`（或等价候选判断）仍可用于 first-use 完整结果、preview rescue 和当前 ArchiveRead page 的候选选择，但它不是 stale placeholder 的生命周期控制器。所有候选替换完成后，Provider context adapter 计算一次包含 system/messages/tools 的最终 context envelope，并将 `request_size_bytes`、`request_budget_bytes` 和 `request_fits` 交给 Agent 的 final capacity gate。gate 失败只产生顶层 bounded diagnostic/终止本次 dispatch，不回写 canonical、不变形已有 placeholder，也不伪造新的 archive ref。

如果 first-use 的 preview、最小 placeholder 和 `capacity_exhausted` fallback 均不能通过同一个候选 `size_fn`，projection 不把超预算的 tool-level error 放入 messages，而是直接以 bounded `ProviderCapacityError` 终止本次 Provider projection；若 fallback 自身 fit，则仍由 projection 返回它，最终 aggregate request fit 继续由 adapter/Agent gate 判定。

### D4：terminal typed projection

terminal display 保留既有 `max_len=500` 的总输出预算，但把预算用于内容优先的 typed text，而不是先序列化完整 JSON 再从开头截断：

- `bounded_ref`：先显示 `preview`（如有）、truncated 状态，再显示 ref、continuation offset 和 `ArchiveRead` 指引。
- `archive_page`：先显示 `page`/content，再显示 offset、next_offset、total_units、unit、has_more。
- `archive_read_error`：显示稳定 error code/type 和 bounded message。
- 未知 envelope：保留现有通用有界渲染。

renderer 必须解析当前 Provider/tool result 的 JSON string 或 mapping，但生成的 terminal text 不回写 canonical event 或 Provider tool content；terminal 不做隐式全文 hydration。即使状态信息较长，也必须给正文/错误保留优先预算，并显式标注截断。

### D5：run 与 invocation metrics

run `started_at_ms` 取该 run 按 canonical ordinal 排序的第一个 `invocation_opened` 事件；后续 opening 只能创建/更新 invocation 记录，不能覆盖 run 起点。run `ended_at_ms` 取 terminal event，若存在多个 terminal 则按事件顺序保留合法最终边界并诊断异常。

run first token 只由明确的 `actions["first_token"]` 或等价 `metadata.lifecycle == "first_token"` 事件产生；`partial=true` 本身不代表 first token。`first_token_ms = first_token_at_ms - started_at_ms`，两者存在时必须非负；缺任一时间返回 null。`duration_ms = ended_at_ms - started_at_ms`，应覆盖整个 run。invocation 的 start/first-token/duration 单独保存，不代替 run 级字段。

### D6：历史兼容、不可变性和错误闭合

旧 SQLite event store 中的重复 final call 是历史事实，不做物理迁移。Session、Model Replay、resume 和其他派生 projection 通过 D1 的 shared helper 稳定保留第一条等价 final fact；不同 payload 产生诊断并 fail closed。新的 run 必须从 writer 层避免重复，不能只靠 projection 去重。

Provider hydration、terminal formatting、ArchiveRead paging 和历史 dedup 均为只读投影，不能改变 event JSON、artifact bytes、metadata、ref 或 digest。缺 ref、scope mismatch、session mismatch、integrity mismatch、closed store 和 capability 缺失时，错误必须 bounded；不得生成无法在当前 scope 读取的成功 ref。

### D7：验证分层与证据格式

P0 证据分四层记录：

1. focused unit/invariant tests：事件、范围、投影、metrics 和 immutability。
2. fresh-process resume test：写入旧重复事件后关闭 store，以新的 Python 进程恢复，证明不重复执行且 Provider context 稳定。
3. final local consumer test：实际 Agent 调用最终 fake Anthropic/OpenAI SDK 或 loopback transport，捕获发送到 SDK 的最终 wire request；不能只调用 projector helper。
4. actual CLI subprocess test：实际入口通过本地 protocol stub 运行，断言 stdout 的 preview/page/error、session.v2 和 resume；不得请求外部网络。

redacted capture 只保留 provider、route、message kind、长度、hash 和必要的 bounded fields，默认不保存 raw body 或 secret。P1 父子 Agent 完整 close barrier、跨进程 capability 传递和广泛历史样本单独记录，不把缺失的 P1 证据冒充 P0 完成。

### D8：逻辑 artifact identity 与内容完整性分离

新工具结果归档使用独立的逻辑 artifact identity。identity 的稳定输入为 `session_id`、`run_id`、`parent_run_id`、可用的 `runtime_event_id`、`tool_call_id`、`tool_name`、发布后内容的 `body_sha256` 和 `rewrite_version`；当没有 runtime event id 时，run id 仍提供同一 session 内的隔离。其派生结果只用于生成新的逻辑 ref。内容文件仍按 `sha256` 存储并可跨逻辑 artifact 去重。

新的逻辑 ref 使用独立前缀并在 metadata 中同时保存 `artifact_id`、`sha256` 和 `size_bytes`。capability 授权以逻辑 ref 的 metadata 为准，因此同一内容在 session A/B 中会得到不同 ref；A 的 ref 不能因为内容相同而读取 B 的 metadata。旧 `artifact:sha256:<digest>` ref 不迁移、不重写，仅保留原有只读解析路径。

### D9：canonical serialization boundary 使用最终 UTF-8 字节数

公共工具结果边界固定为 `MAX_TOOL_RESULT_BYTES = 16 * 1024 * 1024`（16,777,216 字节）。计数流程必须是：

1. 将 `bytes` 规范化为 JSON-safe 的 Base64 binary envelope；
2. 对规范化值使用与 canonical event 相同的紧凑、排序、`ensure_ascii=false` JSON 表示；
3. 对最终 JSON 表示的 UTF-8 编码计算字节数（JSON string 的引号和转义也计入）；
4. `actual_bytes <= 16,777,216` 才允许通过，超过或无法序列化则返回稳定的 bounded error。

`DurableToolBoundary`、普通工具、MCP/特殊工具出口和 `archive_result` 必须调用同一个 helper，不能分别按字符、原始 bytes 或 `str(value)` 计量。Agent 主路径把各 adapter 的 raw value 交给 DurableToolBoundary，使同一结果只做一次规范化和计数；`execute_tool`、`McpConnection.call_tool` 等独立 public wrapper 可以保留，但不得在 Agent 已完成该边界后再次包装/计数。历史导入代码可以保留旧常量名称作为兼容别名，但其不能继续表达字符上限。

### D10：artifact 发布必须可回滚

发布顺序为唯一临时内容文件、内容 fsync/原子发布、逻辑 metadata 原子发布、runtime-store mirror。实现必须跟踪本次调用新建的每个本地文件；后续 metadata 或 mirror 失败时删除本次新建文件，清理失败则报告 recovery-required，且任何失败路径都不返回可被 canonical event 使用的成功 ref。已存在且经完整校验的共享 blob 不得因另一个逻辑 metadata 发布失败而被删除。

孤儿修复仍是显式诊断/运维动作；未知内容不得自动伪造 session/call metadata 后暴露给工具结果 capability。新逻辑 ref 必须在其 metadata 和内容均可读、完整性匹配后才注册到 capability。

## Risks / Trade-offs

- [Provider owner 选择不当] → 非 Provider 入口可能缺少 final fact。统一 `ensure_final_call` 覆盖非 Provider path，并测试 Provider/boundary 同 call 和多工具调用。
- [canonical boundary 低估最终表示] → 统一在规范化和 JSON UTF-8 序列化之后计数，并覆盖 Base64、Unicode、转义和不可序列化值。
- [逻辑 ref 与内容 digest 混用] → 新逻辑 ref 使用独立 metadata 记录和 session-scoped identity；旧 hash-only ref 只走兼容路径。
- [发布失败留下孤儿或错误 ref] → 跟踪新建文件并回滚；共享内容只在本调用新建且没有其他逻辑引用时清理，清理失败输出 recovery-required。
- [identity signature 不稳定] → 历史 dedup 可能误合并。复用 `decode_tool_arguments` 和 canonical JSON；冲突永不覆盖。
- [offset 校验破坏调用方] → 保留 `offset==total_units` 合法 EOF，并为越界提供稳定 `invalid_range`。
- [terminal 输出改变脚本] → 只对已知 envelope 改为 typed formatting，未知工具保持通用路径，并增加真实 stdout 断言。
- [预算计算与实际 SDK tokenizer 有差异] → 明确它是包含 system/messages/tools 的保守本地 context envelope；最终 SDK boundary 测试验证实际发送形状，不能推导外部 Provider 的可用上下文。
- [stale placeholder 随 suffix 变形] → 非 first-use placeholder 路径不参与 aggregate fit；final capacity gate 在 projection 后独立阻止超预算 dispatch，并用追加 suffix 的临界预算回归测试锁定单调性。
- [capacity fallback 自身超预算] → 所有 tool-level fallback 经过最终 context fit；若错误 envelope 也放不下，则返回顶层 bounded capacity verdict，不把超预算错误发给模型。
- [redacted capture 泄漏] → 只允许字段级 redaction，测试产物不写 raw body、key、路径或 traceback。
- [历史重复事实无法删除] → 通过派生 projection 兼容，保留 source digest；回滚旧版本可能重新产生重复，因此只能作为短期诊断。

## Migration Plan

1. 先更新本 change 的 proposal/design/spec/tasks，冻结 P0 文件范围、事件 identity、ArchiveRead 边界、预算公式和真实消费者验收方式。
2. 先加入失败回归测试和真实本地消费者夹具，再实现 D1、D2、D5 的低层修复与 D4 terminal formatting；同时实现 D8-D10 的 artifact identity、字节边界和回滚契约；旧 artifact ref/digest 保持兼容。
3. 实现 Provider 双后端最终 SDK boundary 验证，确认首请求、capacity rescue、Unicode/bytes preview、stale、ArchiveRead page、无 capability 和 capacity gate 的最终 wire content；fit 断言必须覆盖 SDK 送出的 system/messages/tools context envelope，并验证 gate 失败时不 dispatch。
4. 运行 focused tests、fresh-process resume、CLI subprocess、全量 Python tests、compileall 和 `git diff --check`；分别报告 local/fake/real-local/external evidence，并记录任何缺少真实依赖的阻断。
5. P1 父子 Agent close/capability 场景只在有额外范围授权后推进；本批次不因 P1 未完成而修改普通 child allowlist。
6. 主 Agent 复核 frozen diff、P0 acceptance 和残余风险后，另行决定是否授权 Git 交付；本 change 不包含提交或发布动作。

## Open Questions

- 本 change 不保留影响 P0 实现的未决设计问题。若未来模型上下文预算或 terminal 500 字符上限要改变，必须新建/修订 change 并重新冻结 formula/consumer evidence。
- 将来若把既有 Maka capability 同步到 `openspec/specs/`，应把本 change 的完整性 requirements 合并为 modified capability；当前主规格目录为空，因此本 change 保持独立。
