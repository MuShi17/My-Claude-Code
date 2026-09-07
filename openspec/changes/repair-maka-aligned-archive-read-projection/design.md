## Context

本变更承接批次总览和技术草案，目标仓库基线为 D:/workspace/My-Claude-Code main@5c71042。该提交已经修复了 Agent-owned Runtime Store 与 Artifact Archive 的会话生命周期，但大工具结果的归档写入和模型读取仍然是两条断开的路径。

当前实现边界如下：

- ArtifactArchive 已按内容地址写入正文和 metadata，并支持 inspect/read；ArtifactRef.placeholder() 只包含 bounded_ref、hash、size、encoding、scope 等 metadata。
- DurableToolBoundary 默认在约 16 KiB 后归档大结果，并把 placeholder 写入 canonical tool outcome。
- provider_content.materialize_tool_result() 只把已有值格式化成 Provider 内容；provider_context 和 ModelReplayProjection 没有根据 ref 读取 artifact 的能力。
- Anthropic/OpenAI-compatible agent loops 都会把 boundary 返回值直接追加到 Provider 历史并单独格式化；ArchiveRead 没有工具定义或 handler。
- subagent.py 的普通 child allowlist 只筛选 tool_definitions；父 Agent 传入 archive store/sink 并不等于子代理具备受作用域保护的读取能力。
- tools.py 还存在 50,000 字符的下游字符串截断，它只能改变终端/工具返回文本，不能提供可恢复的 artifact ref。
- canonical replay 需要继续保存事实和安全引用；不能通过重写 canonical event 来迎合 Provider 或终端展示。

Maka 对照快照 D:/workspace/maka@57e08d83497d1d7ace7d6eff88e4e5267a0345b5 的关键语义是：Read 在没有分页参数时保留完整内容；active prune 不在首次请求前无条件裁剪最新结果；stale 结果变为带 resource ref 和读取指引的 placeholder；ArchiveRead 是由运行时 capability 提供的独立读取能力；终端不把 placeholder 当成正文。

## Goals / Non-Goals

**Goals:**

- 保留现有 canonical event 和 artifact ref 兼容性，使 Provider 能在当前生命周期中消费 archived result。
- 为 Provider 首次请求实现 Maka 对齐的三分支：预算足够时完整安全正文、预算不足时前 N 字符容量救援预览、stale 时有 ArchiveRead 指引的 bounded placeholder。
- 以 ToolResultArchiveCapability 统一 writer、reader、decoder 和 scope，向父 Agent/子代理派生只读 ArchiveRead。
- 支持 bounded inspect/read/query、offset/limit、hash/size/session/lineage 校验和结构化错误。
- 保持 Anthropic 与 OpenAI-compatible 的 Provider 语义一致，并将终端投影与 Provider projection 分开。
- 对 ArchiveRead、首次请求、stale、父子 Agent、store close、redaction 和 double-bounding 建立真实 fake-consumer 回归证据。

**Non-Goals:**

- 不移植 Maka 的完整 runtime host、graph、UI、continuation 或跨进程 artifact service。
- 不删除、迁移、批量修复或改变既有 artifact 文件、Canonical Event、SQLite schema 或 artifact ref 格式。
- 不允许任意文件路径、任意 session 或任意全局 SHA-256 读取。
- 不通过调大 16 KiB boundary、关闭 archive 或取消 response cap 规避上下文压力。
- 不改变副作用工具的 dispatch、outcome-unknown、retry、recovery 或权限确认语义。
- 不执行 push、MR、merge、部署、release 或修改 D:/workspace/maka。

## Decisions

### 1. 以现有 ArtifactArchive 为事实存储，新增运行时 capability

新增 src/mini_claude/archive_capability.py，定义 ToolResultArchiveCapability 及其受限 reader/decoder；ArtifactArchive 继续负责 content-addressed 文件、metadata、redaction 和低层完整性校验。

Capability 至少携带：

- archive 实例；
- 当前 session_id；
- 当前 Agent 的 lineage/run scope；
- 允许读取的父子 lineage 集合；
- bounded page 的 default/max limit；
- ArchiveRead 的 tool definition 和 handler。

ArtifactArchive 增加面向 capability 的 read_page 或等价接口，但不把任意本地路径暴露给上层。对文本 artifact，offset/limit 使用解码后的字符单位；对 binary artifact，使用字节单位并返回受限的可消费表示。每次读取都先验证 ref、metadata、scope 和 content hash/size，再产生 bounded response。

选择 capability 而不是在 tools.py 中注册全局 ArchiveRead 的原因：

- 读取权限属于一次 runtime/session，而不是所有 Agent 的全局工具权限；
- parent/child 可以派生 scope，而不需要扩展普通 child allowlist；
- writer、reader、decoder 若缺一，创建 placeholder 时可立即 fail closed；
- 未来可替换 archive backend，而不让 Provider 直接依赖文件系统。

备选方案是把 ArchiveRead 加入全局 tool_definitions，并在工具执行时用 session id 做额外判断。该方案会让无 archive capability 的 Agent 广告不可用工具，也会把运行时资源权限混入静态 allowlist，因此不采用。

### 2. 保持 canonical placeholder，新增请求时的 archive-aware Provider projection

不把完整大结果重新写入 canonical event。DurableToolBoundary 仍可同步归档，并在 canonical tool outcome 中保存 bounded_ref；Provider 请求构建阶段通过 capability 解析该 ref，按当前消息预算生成 provider_projection。

projection 的决策顺序：

1. 判断该 tool result 是否为当前 Provider request 的最新可消费结果，且尚未经历后续 step/请求使其 stale。
2. 计算当前 Provider 请求中可给该结果使用的有效预算，预算包含已有 system prompt、历史消息、tool schema、封装和输出留白。
3. 若 artifact 的安全物化正文在预算内，读取并投影完整正文。
4. 若完整正文放不下，读取前 N 个安全字符，投影 capacity-rescue envelope：preview、truncated、preview_chars、omitted_chars、next_offset、ref、size/encoding 和 ArchiveRead 指引。
5. 若结果已 stale，投影 bounded placeholder；不因为旧结果已归档而再次内联全文。
6. 若连容量救援 envelope 也放不下，保留最小 ref、truncated 和 ArchiveRead 指引。

这保留 canonical fact 和 provider-visible projection 的分离：canonical event 可始终记录完整的 artifact identity，Provider 每一轮根据消费状态和预算选择视图。

选择在 projection boundary 读取而不是让 DurableToolBoundary 返回完整字符串的原因：

- 不会把完整大结果复制进持久化 canonical event；
- 可以复用现有 artifact ref 和生命周期修复；
- stale/compaction/first-use 可以由同一 replay projection 决定；
- 归档写入失败仍能沿用既有无悬空 ref 的 fail-closed 语义。

### 3. 冻结默认容量参数，容量救援只作为真实不足的后备

本 change 使用以下默认值，均可由构造参数覆盖但必须保持正数和最大值校验：

- ARCHIVE_PREVIEW_CHARS = 4,000；
- ARCHIVE_READ_DEFAULT_LIMIT = 6,000；
- ARCHIVE_READ_MAX_LIMIT = 7,500；
- ArchiveRead response envelope 有独立的最大字符/字节预算，不能只依赖 provider_content 的 50,000 字符截断。

这些数字是 Mini 的安全默认值，不声称是 Maka 的公共 API；必须保持的 Maka 行为是“首请求容量允许时不主动剪枝，容量不足时才给出可恢复的 bounded preview，stale 时显式读取”。

next_offset 返回实际已解码字符数/字节数，而不是盲目返回配置值。对文本结果不能截断 UTF-8 字节序列；对结构化 JSON 不能让 preview 伪装成可解析的完整对象。

### 4. ArchiveRead 使用固定操作合同和结构化错误

ArchiveRead 的输入为：

- operation：inspect、read 或 query；
- ref：artifact:sha256:<digest>；
- offset：非负整数，read 时默认 0；
- limit：正整数，read 时默认 6,000 且不得超过 7,500；
- query：query 操作的受限查询字段；
- 可选 expected_sha256/expected_size_bytes，用于防止引用元数据被替换。

输出为有界 JSON-compatible result。inspect 返回 metadata；read 返回 page、offset、next_offset、has_more 和 artifact metadata；query 只返回已登记 metadata 中允许的有限字段。错误使用稳定 code：not_found、metadata_invalid、integrity_mismatch、session_mismatch、scope_denied、invalid_range、limit_exceeded、not_queryable、archive_store_closed、capability_unavailable。

错误消息不返回 archive 根目录、Python traceback、任意本地路径或其他 session 的 metadata。ArchiveRead 不接受 file_path，不执行重新归档，不进行隐式全文 hydrate。

### 5. Provider 工具注册由 capability 派生，普通权限 allowlist 保持不变

Agent 的 effective provider tools 在创建请求时由基础工具集合和当前 capability 的 ArchiveRead definition 组成：

- 有 capability：向 Anthropic/OpenAI-compatible 请求广告一个同名 ArchiveRead；
- 无 capability：不广告 ArchiveRead，且不能生成需要它的成功 placeholder；
- 自定义 tool 与保留名 ArchiveRead 冲突时 fail closed，不选择任意一个静默覆盖；
- ArchiveRead 只读、不需要普通确认，但仍走 runtime capability 的 scope/limit 校验；
- tools.py 的静态 READ_TOOLS/CONCURRENCY_SAFE_TOOLS 只在不破坏 capability 边界的前提下补充 runtime read-only 处理。

子 Agent 构造接收父 capability 的派生句柄，而不是把 ArchiveRead 放进 subagent.py 的普通 allowed_tools 筛选。派生句柄带 session/lineage grant，子代理结束 turn 不得关闭 caller-owned archive/store。父 Agent 的 capability 必须覆盖子代理返回结果可能需要的 ref，防止 bounded_ref 套 bounded_ref。

### 6. 通过统一的 neutral result projector 适配两个 Provider

在现有 ModelReplayProjection/CanonicalModelContextAdapter 之上增加 archive-aware projection 参数或独立 adapter，使两条 Provider 路径共享中立的 result view，再由 Anthropic/OpenAI formatter 负责各自 tool result 包装。

中立 view 至少区分：

- canonical_result：事件中的安全原值/引用和 runtime_event_id；
- provider_projection：完整正文、capacity-rescue preview 或 stale placeholder；
- terminal_projection：终端有界正文、状态和读取提示。

不能在 Anthropic formatter 中读取 archive、在 OpenAI formatter 中只序列化 ref；两者必须输入相同的 neutral view。provider_content 的普通结构化/图片/字符串校验继续生效。

live tool loop 在把 boundary outcome 写入 Provider history 前使用该 adapter；cold replay、resume 和 compaction 后重建 Provider context 时也使用同一 adapter。历史 placeholder 没有 read_instructions 时由 ref metadata 补充兼容提示，但不改写历史文件。

### 7. 终端投影只展示有界可读信息

终端不直接显示 Provider 原始 metadata JSON，也不因为发现 ref 就全文读取 archive。

- 小结果显示正文；
- 大结果显示有限 preview 或 stale 状态、size/ref 和 ArchiveRead 说明；
- ArchiveRead 返回页时显示该页，仍受终端上限保护；
- archive error、reader error、scope error 和 store closed 使用不同状态；
- redaction 在 archive 写入和 projection 读取两侧都保持一致。

这允许终端和 Provider 有不同预算，同时禁止终端输出反向改变 canonical/provider 事实。

### 8. 生命周期和 fail-closed 约束

本批次沿用 5c71042 的 Agent 会话级 Store/Archive 生命周期：

- ArchiveRead 的 handler 在 request/child task 完成前保持 capability 有效；
- Agent.aclose() 之前等待未完成的 reader/provider tasks；
- caller-owned archive/store 由调用方关闭；
- store close 后的读取返回 archive_store_closed，不能偷偷 reopen 另一个 store；
- archive write、metadata mirror 或 read-back 失败时不能产生可行动成功 ref；
- parent/child 返回路径必须在父 Agent 仍持有 capability 时完成 projection。

### 9. 测试分层和独立验收

实现先补 Item 02 的策略和测试，再按以下层级验证：

- ArtifactArchive/ArchiveCapability 单元：page、offset/limit、hash、scope、errors；
- provider projection 集成：Anthropic 与 OpenAI fake consumer 捕获最终请求消息；
- lifecycle 集成：parent/child、取消、close barrier、store closed；
- terminal/CLI：有界 preview、placeholder 状态和 redaction；
- 既有 Canonical Event、Compaction、Resume、权限和工具副作用回归；
- OpenSpec strict、git diff --check 和完整 Python 测试。

fixture 或内部对象断言不能替代 fake consumer 的最终消息断言；本地证据不能声称真实外部 API、部署或新会话验收。

## Risks / Trade-offs

- [读取阶段为了校验 hash 可能读取较大文件] → Provider response 和 ArchiveRead 返回严格 bounded page；优先复用已有完整性校验，必要时使用流式 hash/page 实现，不能取消校验。
- [现有 canonical replay 只保存 placeholder，projection 可能遗漏最新/stale 状态] → 保留 runtime_event_id/high-water 和 tool-call 顺序，在中立 projector 统一判定；为 first-use/stale 建立双 Provider 消费测试。
- [Provider 有效容量估算与实际 API token 计费不完全相同] → 使用现有 effective_window 和序列化字符预算作为本地 fail-safe；不足时降级 preview，不主动扩大窗口；记录真实请求捕获证据。
- [自定义工具与 ArchiveRead 同名] → capability 初始化时冲突即失败，不静默替换工具。
- [子代理共享 archive 生命周期复杂] → capability 派生不转移 ownership，close barrier 测试覆盖父继续消费和子先结束。
- [旧 artifact metadata 缺少读取指引] → projection 根据兼容 ref 生成默认 hint，不迁移历史 artifact。
- [终端和 Provider 逻辑再次分叉] → 两者共享 canonical metadata/redaction，但保留独立 projection 函数和各自消费者测试。

## Migration Plan

1. 先创建并校验本 change 的 proposal、spec、design、tasks；在代码实现前冻结 ref、scope、page 和 first-use 分支。
2. 新增 capability 和 ArchiveRead 低层测试，保持现有 ArtifactArchive ref/文件兼容。
3. 接入 neutral provider projection，再接入 Anthropic/OpenAI live loop、cold replay/resume 和 compaction。
4. 接入父子 capability 派生和 terminal projection；运行 focused/full/OpenSpec 校验。
5. 回滚时只回滚本 change 的代码、测试和 OpenSpec 文件；不删除已有 archive、runtime.sqlite 或 canonical session 文件。
6. 本 change 不需要数据迁移、外部依赖或部署步骤；真实 API/部署验收若要执行，另行取得 delivery/runtime authority。

## Open Questions

无。以下决策在本设计中冻结：

- ArchiveRead 的 Provider-visible 名称为 ArchiveRead；
- ref 继续使用 artifact:sha256:<digest>；
- 默认 preview/read/max page 分别为 4,000 / 6,000 / 7,500；
- 首次请求预算足够时完整安全正文优先，只有容量不足才 preview；
- stale 结果使用 placeholder + ArchiveRead；
- 子代理通过 capability 派生，不通过普通 allowlist 注入；
- Agent 关闭后不可继续读取，调用方拥有的 store/archive 不由子代理关闭。

