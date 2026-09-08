## ADDED Requirements

### Requirement: Provider first-use projection SHALL 优先发送完整安全结果

Provider projection SHALL 从 canonical event 重建中立 tool-result view，并以首次包含该 tool result 的 Provider request 作为 first-use 判定。若完整安全结果适合最终 Provider request，projection SHALL 直接发送完整结果，不写 archive、不生成 placeholder，也不修改 canonical event。

#### Scenario: Anthropic 首次请求容量足够

- **WHEN** 一个新完成的安全 tool result 首次进入 Anthropic Provider request 且最终序列化 payload 能容纳完整结果
- **THEN** Anthropic tool result 包含完整安全正文，不是裸 `bounded_ref`，且本次 projection 不调用 archive writer

#### Scenario: OpenAI-compatible 首次请求容量足够

- **WHEN** 同一类新完成结果首次进入 OpenAI-compatible Provider request 且最终 payload 能容纳完整结果
- **THEN** OpenAI-compatible tool result 具有等价的完整安全正文语义，不因 provider formatter 不同而提前归档

#### Scenario: Provider projection 不改写 canonical

- **WHEN** projection 为容量判断复制并转换 canonical tool result
- **THEN** canonical event、result digest、原始安全内容和 event 顺序保持不变

### Requirement: 最新 completed provider step SHALL 在首次消费前保持完整

archive-aware live loop MUST 在 Provider context refresh 之前跳过旧的利用率截断、stale snip 和 microcompact 工具结果改写。最新 completed provider step 的工具结果 SHALL 保持 canonical replay 中的完整安全正文，直到第一次包含它的 Provider request 成功返回；之后才可由 archive projection 按 stale/容量规则投影。完整 compaction 仍可通过独立的 canonical context transition 发生，但不得由旧工具结果压缩旁路提前替换该正文。

#### Scenario: live loop 不在首次请求前截断新结果

- **WHEN** 一个工具刚完成，随后 Provider loop 在高输入利用率或空闲条件下准备下一次请求
- **THEN** live loop 不先执行旧 Tier 1/2/3 工具结果改写，Provider projection 先看到完整安全结果；若最终 payload fit 则直接发送正文

#### Scenario: Provider 请求失败时保留完整结果供重试

- **WHEN** 首次包含新工具结果的 Provider request 失败或需要重试
- **THEN** canonical replay 和下一次 Provider request 仍保留完整安全结果，不因失败路径提前写入截断 replacement 或归档 placeholder

#### Scenario: 同一用户轮次的多个 completed step 只保护最新组

- **WHEN** 一个用户输入先后完成两个或多个 assistant/tool 组，且 replay 中这些结果位于同一用户边界之后
- **THEN** 只有最后一个连续完整组的 tool call id 被标记为 first-use；较早组可以投影为 stale placeholder，最新组在首次 Provider request 前保持完整，不因跨组集合比较而被误判为 stale

### Requirement: ArchiveRead 返回结果 SHALL 有界且不可递归归档

projection MUST 根据 `tool_call_id` 解析 `ArchiveRead` 工具调用。`ArchiveRead` 的 page、metadata、query 和 error envelope 已经是 capability 产生的有界控制结果：适合最终 Provider payload 时 SHALL 原样发送，不得调用 `archive_result()`；不适合时 SHALL 返回有界、可行动的 `capacity_exhausted`，不得把该页面再次写入 archive 或生成新的嵌套 ref。历史 `ArchiveRead` bounded ref 只能复用/读取原 ref，不能创建第二层 artifact。

#### Scenario: ArchiveRead page 在请求中可容纳时保持正文

- **WHEN** 模型调用 `ArchiveRead(read)` 得到 bounded page，页面与其余消息可以放进最终 Anthropic 或 OpenAI-compatible request
- **THEN** Provider tool result 仍是 `archive_page` 正文，projection 不新增 artifact、不改变 ref、不把 page JSON 包装成新的 `bounded_ref`

#### Scenario: ArchiveRead page 无法容纳时不创建嵌套 ref

- **WHEN** 已有界的 ArchiveRead page 连 aggregate Provider payload 都放不下
- **THEN** Provider 收到稳定的 `archive_read_error`/`capacity_exhausted`，可指向原 artifact ref 并提示缩小 `limit`；重复 projection 不增加 artifact，不递归增长 `size_bytes`

#### Scenario: 历史 ArchiveRead bounded ref 不再二次归档

- **WHEN** replay 遇到旧版本遗留的 `ArchiveRead` `bounded_ref`
- **THEN** projection 至多读取/复用该 ref，不调用 archive writer 创建新 ref；无法读取时返回原有界错误，不把错误 envelope 再归档

### Requirement: Provider 容量救援 SHALL 只在完整结果确实不适合时建立归档

当首次包含结果的最终 Provider request 无法容纳完整安全结果时，projection SHALL 在当前有效 capability 下按需归档完整安全结果，并返回不超过 4,000 字符的前缀 preview、`truncated=true`、ref、实际 `next_offset` 和可调用的 ArchiveRead 指引。若 envelope 仍无法容纳，系统 SHALL 缩短 preview；归档失败时不得发送成功的悬空 ref。

#### Scenario: 首次容量不足返回可恢复 preview

- **WHEN** 新完成的完整安全结果无法放入首次 Provider request，但 archive writer 和 ArchiveRead capability 有效
- **THEN** Provider 收到前缀 preview、`truncated=true`、合法 artifact ref、实际 preview 计数和 ArchiveRead read 指引

#### Scenario: preview 使用实际 Unicode continuation offset

- **WHEN** 文本 preview 在 Unicode 字符边界截取且实际返回字符数少于配置上限
- **THEN** `next_offset` 等于实际返回字符数，后续 ArchiveRead 可以从该 offset 连续读取

#### Scenario: rescue envelope 仍超出容量

- **WHEN** 当前有效 Provider budget 连最小 preview envelope 也无法容纳
- **THEN** projection 返回最小但可行动的 ref、截断状态和 ArchiveRead 指引；若没有有效 ref 则返回受控错误而非不可读成功结果

#### Scenario: archive 失败不产生悬空引用

- **WHEN** projection-time archive、metadata commit、read-back 或 hash 校验失败
- **THEN** Provider 收到有界 archive error 或仍适合的安全 inline result，不收到宣称成功但当前 capability 无法读取的 ref

### Requirement: stale tool result SHALL 使用可行动的 archive placeholder

当后续 completed step、Provider request、compaction transition 或等价上下文事件使结果 stale 时，projection SHALL 用有界 placeholder 替换旧正文。placeholder SHALL 包含经过校验的 ref、必要 metadata 和 ArchiveRead 指引，不得自动重新内联 stale 全文。

#### Scenario: 后续 step 使旧结果 stale

- **WHEN** 一个 tool result 后出现新的 completed model step，且下一次 Provider projection 仍需携带该历史结果
- **THEN** Provider 收到带 ref 和 ArchiveRead 指引的 bounded placeholder，而不是旧结果全文

#### Scenario: stale 结果尚未归档时按需归档

- **WHEN** canonical 中保存的是完整安全 tool result，且它第一次进入 stale projection 时还没有 artifact ref
- **THEN** projection 在生成 placeholder 前按需建立可读取 artifact ref，成功后才发送 placeholder

#### Scenario: 历史 placeholder 缺少指引

- **WHEN** replay 遇到合法历史 `bounded_ref` 但没有 `read_instructions`
- **THEN** projection 从已校验 metadata 补充默认 ArchiveRead 指引，不修改历史 canonical event

### Requirement: ArchiveRead SHALL 提供有 scope 的 bounded inspect/read 闭环

当前 Agent 有 `ToolResultArchiveCapability` 时，Provider tool set SHALL 广告唯一的 `ArchiveRead`。`inspect` SHALL 返回受保护 metadata，`read` SHALL 返回有界页；默认页为 6,000，最大页为 7,500。文本页使用字符 offset，二进制页使用字节 offset；ArchiveRead MUST 拒绝 file path 和无 scope 的全局 digest 读取。

#### Scenario: 有 capability 时发现 ArchiveRead

- **WHEN** Agent 使用有效 archive capability 构建 Anthropic 或 OpenAI-compatible Provider request
- **THEN** 请求广告 exactly one `ArchiveRead` tool，其 schema 包含 operation、ref、offset、limit 和受限 query 输入

#### Scenario: inspect 和 read 返回有界结果

- **WHEN** 模型使用合法 artifact ref 先调用 `inspect`，再调用 `read` 且 limit 不超过 7,500
- **THEN** inspect 返回安全 metadata，read 返回带 offset、next_offset、has_more 和单位的 bounded page，不返回无界全文

#### Scenario: 非 artifact 路径输入被拒绝

- **WHEN** ArchiveRead 收到本地 file path、Glob 或非 `artifact:sha256:<digest>` 的 ref
- **THEN** 工具返回稳定的参数/not_found 错误，不读取本地路径或其他资源

#### Scenario: scope 和完整性校验失败关闭

- **WHEN** ref 属于其他 session/lineage，或 metadata、hash、size 与内容不一致，或 store 已关闭
- **THEN** ArchiveRead 返回 session/scope/integrity/archive_store_closed 错误，不泄露 metadata，不 reopen 或替换 store

### Requirement: 父子 Agent SHALL 通过 capability 派生 ArchiveRead 且保持生命周期所有权

父 Agent SHALL 向获授权子代理传递只读派生的 archive capability；普通 child allowlist 不得因本 capability 自动扩大。派生 capability SHALL 保留 session/lineage scope，子代理不得关闭 caller-owned archive 或 runtime store；父子返回完成前 capability 必须有效。

#### Scenario: 子代理读取授权范围内的 artifact

- **WHEN** 子代理收到父 capability 的只读派生句柄并读取授权 lineage 内的 artifact ref
- **THEN** 子代理获得与父 Agent 等价的 bounded、redacted ArchiveRead 结果

#### Scenario: 子代理越权读取被拒绝

- **WHEN** 子代理请求其他 session 或未授予 lineage 的合法 ref
- **THEN** ArchiveRead 返回 session_mismatch 或 scope_denied，不返回 artifact 内容

#### Scenario: 子代理结束不关闭父资源

- **WHEN** 子代理先于父 Agent 完成 turn，父 Agent 仍需继续 projection 或 ArchiveRead
- **THEN** 子代理结束不关闭或替换父 Agent 持有的 archive/store，父 Agent 仍能完成后续读取

### Requirement: terminal projection SHALL 与 Provider/canonical view 分离

终端 SHALL 使用独立的有界 terminal projection：小结果可显示完整安全正文，大结果或 stale 结果显示 bounded preview/status、ref 和 ArchiveRead 指引。终端不得因看到 ref 隐式全文 hydrate，也不得把 metadata JSON 冒充文件正文或反向写回 Provider/canonical view。

#### Scenario: 小结果终端显示正文

- **WHEN** 工具结果较小且不存在 archive placeholder
- **THEN** 终端显示完整安全正文，而不是仅显示 archive metadata

#### Scenario: 大结果终端显示可行动提示

- **WHEN** terminal projection 收到大结果、capacity preview 或 stale placeholder
- **THEN** 终端显示有界内容/状态、ref 和清晰 ArchiveRead 指引，不隐式倾倒整个 artifact

#### Scenario: terminal formatting 不改变 Provider 内容

- **WHEN** 终端格式化一个 bounded result 或 ArchiveRead error
- **THEN** Provider projection 仍由 canonical neutral view 独立生成，不把终端状态文本当作 tool result

### Requirement: archive and lifecycle failure SHALL fail closed

archive write、metadata commit、read-back、decoder、Provider projection、ArchiveRead 和 store lifecycle failure SHALL 返回稳定有界错误或仍然适合的安全 inline result，不得发布当前 capability 无法读取的成功 ref。关闭前 SHALL 等待 Agent-owned pending operations，caller-owned store SHALL 由 caller 关闭。

#### Scenario: runtime store 在 metadata commit 时关闭

- **WHEN** oversized result 的 projection-time archive 在 metadata commit 前后遇到 `runtime store is closed`
- **THEN** canonical 事实不被改写为虚假成功，Provider/terminal 收到有界 archive error，且不留下悬空 ref

#### Scenario: pending ArchiveRead 在 close barrier 前完成

- **WHEN** Agent 或子代理退出时仍有 Provider projection 或 ArchiveRead task
- **THEN** 所有 caller-visible task 在 Agent-owned store 关闭前 settle，关闭后新的读取返回 archive_store_closed

#### Scenario: 双 Provider 错误形状保持安全一致

- **WHEN** Anthropic 和 OpenAI-compatible projection 遇到相同的 missing ref、scope、integrity 或 lifecycle failure
- **THEN** 两条路径都返回等价的稳定错误 code 和 bounded message，不泄露 traceback、archive 根路径、secret 或其他 session 内容
