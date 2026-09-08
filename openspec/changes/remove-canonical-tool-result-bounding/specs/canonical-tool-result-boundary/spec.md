## ADDED Requirements

### Requirement: 所有工具结果 SHALL 经过统一的 16 MiB（16,777,216）canonical JSON 字节公共上限

系统 SHALL 在内置工具、MCP 工具、子代理对外结果和其他公共工具结果适配路径汇合处执行统一的结果规范化与序列化字节上限检查。`bytes` SHALL 先规范化为 Base64 binary envelope；所有结果再按紧凑 canonical JSON 的 UTF-8 字节数计量，JSON 字符串的引号和转义也计入。结果序列化长度小于或等于 16,777,216 字节（16 MiB）时允许继续进入 canonical boundary，超过或无法序列化时 MUST 返回受控的 `result_too_large` 或 `result_not_serializable` 工具错误，不得静默首尾截断。

#### Scenario: 精确公共上限以内的结果被接受

- **WHEN** 任一工具返回规范化后的 canonical JSON UTF-8 序列化长度为 16,777,215 或 16,777,216 字节的结果
- **THEN** 系统允许该结果继续执行 redaction、canonical append 和后续 Provider projection，且不插入首尾截断标记

#### Scenario: 所有工具路径统一拒绝超限结果

- **WHEN** 内置工具、MCP 工具或子代理适配路径返回规范化后长度为 16,777,217 字节的结果
- **THEN** 公共结果边界返回 `result_too_large`，包含工具名、`limit_bytes=16777216` 和实际字节数，并且不把该结果静默写成头尾拼接内容

#### Scenario: 超限错误本身保持安全和有界

- **WHEN** 公共结果边界生成 `result_too_large` 错误
- **THEN** 错误不包含 secret、traceback、本地 archive 根路径或其他 session 内容，且错误 envelope 自身不超过公共上限

#### Scenario: 公共上限不替代 Provider 容量

- **WHEN** 一个不超过 16,777,216 canonical JSON 字节的工具结果进入有效 Provider request
- **THEN** 系统仍根据最终 Provider payload 的 token/byte budget 独立决定完整投影、容量救援或 stale placeholder

### Requirement: canonical tool result SHALL 保存完整的安全结果

对于已通过公共 16,777,216 canonical JSON 字节上限的结果，canonical `function_response.result` SHALL 保存完整的脱敏结果。`DurableToolBoundary` MUST NOT 使用 16 KiB `max_result_bytes` 立即归档、替换为 `bounded_ref` 或静默截断普通内容；保留历史引用兼容性，但不得把新结果无条件写成引用。

#### Scenario: 超过旧 16 KiB boundary 的结果仍完整保存

- **WHEN** 一个普通非敏感工具结果的 canonical JSON UTF-8 序列化长度超过 16 KiB 但不超过 16,777,216 字节，并完成 canonical append
- **THEN** `tool_outcome` 和 `function_response` 的 result 均包含完整安全结果，且 durable boundary 未因 16 KiB 调用 ArtifactArchive 生成 placeholder

#### Scenario: redaction 不再裁剪普通大字符串

- **WHEN** canonical tool result 中包含超过 `max_string_chars=8192` 的普通字符串或嵌套普通字符串
- **THEN** redaction 保留其完整内容，不生成 `ref=inline:*` 或无真实 archive 保证的 `bounded_ref`

#### Scenario: secret redaction 仍然生效

- **WHEN** canonical tool result 同时包含普通大文本和 secret marker、敏感 key 值或受保护路径
- **THEN** 普通大文本完整保留，敏感部分按现有 redaction policy 替换，且不得因为保留完整文本而泄漏 secret

#### Scenario: canonical 二次准备保持结果一致

- **WHEN** 同一 tool result 先经过 `_outcome()` 再经过 `CanonicalSink` 的事件准备
- **THEN** 两次准备产生一致的完整脱敏 result，不出现第一次完整、第二次 inline ref 或首尾截断的 double-bounding

### Requirement: 超过公共上限 SHALL 返回可诊断工具错误而不是伪造成功结果

系统 SHALL 为公共结果超限提供稳定的错误类型和诊断字段。对于 `read_file`，错误 SHALL 提示使用较小的 `offset` / `limit`；其他工具不因本 capability 自动获得未定义的分页接口。

#### Scenario: read_file 超限提示分页

- **WHEN** 无分页参数的 `read_file` 结果的 canonical JSON UTF-8 序列化超过 16,777,216 字节
- **THEN** 工具返回 `result_too_large` 和 `offset` / `limit` 分页提示，不返回静默首尾混合文件内容

#### Scenario: 非 read_file 工具使用统一错误契约

- **WHEN** run_shell、grep、list、web 或 MCP 工具结果的 canonical JSON UTF-8 序列化超过 16,777,216 字节
- **THEN** 工具返回相同类别的受控超限错误，不隐式创建文件分页或 ArchiveRead ref

#### Scenario: Base64 and non-serializable values use the same boundary

- **WHEN** a binary result expands beyond 16,777,216 bytes after Base64 normalization, or a result is circular/non-JSON-serializable
- **THEN** the common boundary returns a payload-free bounded error and does not use an under-counting string representation

### Requirement: 历史 bounded_ref SHALL 保持兼容读取

Provider projection 和 ArchiveRead SHALL 继续识别历史 canonical event 中已经存在的合法 `bounded_ref`。兼容处理可以补充安全的默认读取指引，但 MUST NOT 批量重写历史 event 或把全局 digest 当成无 scope 的读取授权。

#### Scenario: 历史合法引用继续可投影

- **WHEN** session replay 遇到旧格式且 ref、metadata、hash 和 scope 均有效的 `bounded_ref`
- **THEN** projection 可以生成完整、preview 或 actionable placeholder，并且 ArchiveRead 能在授权范围内读取

#### Scenario: 历史无读取指引时只补充投影信息

- **WHEN** 历史 `bounded_ref` 缺少 `read_instructions` 但 ref metadata 通过校验
- **THEN** 系统只在当前 Provider/terminal projection 中生成默认指引，不修改历史 canonical event
