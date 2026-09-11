## Why

当前 Rollo Code 可能在首次 Provider 请求前就把工具结果写成 `bounded_ref`，同时工具执行、durable boundary、redaction 和 Provider projection 还存在多个相互独立的尺寸边界。结果是模型、子代理和终端可能只能看到不可直接消费的 metadata，且 canonical event 无法再恢复原始安全结果。该变更依据 ISS002 对齐 Maka 的 first-use、stale projection 和 ArchiveRead 语义，并明确 16 MiB（16,777,216 字节）的 canonical JSON UTF-8 字节上限是所有工具共用的公共结果安全上限。

## What Changes

- **BREAKING** 移除 canonical 工具结果写入路径上的 16 KiB 立即归档/placeholder 替换，以及普通工具结果的静默首尾截断；canonical 保存完整的脱敏结果，历史 `bounded_ref` 保持兼容读取。
- **BREAKING** 将所有经过公共工具结果边界的工具结果统一限制为 16 MiB（16,777,216）canonical JSON UTF-8 字节；超过上限返回统一、可诊断的受控工具错误，不再返回静默首尾混合内容。
- 让 canonical redaction 只负责隐私脱敏，不因 `max_string_chars` 把普通大工具结果变成无真实归档保证的 `inline:*` 引用。
- 将 archive 写入、容量救援和 stale placeholder 决策放入 Provider projection；首次包含结果的请求能容纳时发送完整安全结果，确实无法容纳时发送前缀预览、ref、截断状态和可调用的 ArchiveRead 指引。
- 对齐 Maka 的 active-step 语义：最新完成的 Provider step 在第一次可能发送给模型前保持完整；live loop 不再先用旧的利用率/空闲压缩管线改写该结果。
- 修正同一用户轮次包含多个 Provider step 时的 `first-use` 判定：只把最后一个连续完整的 assistant/tool 组视为 active step，较早组才进入 stale projection，不能把整轮所有 tool result 聚合后与第一组 tool call 比较。
- 将 `ArchiveRead` 返回的 bounded page/metadata/error 视为已经有界的控制结果，禁止通用 projection 再次归档；若整页连当前请求都放不下，返回可行动的容量错误，而不是制造新的嵌套 ref。
- 为 `read_file` 增加按 Maka 语义工作的可选 `offset`、`limit` 行分页参数；无参数时读取完整文件，超限时提示分页。
- 保持 ArchiveRead 的 session/lineage scope、hash/size 校验、response cap、父子 capability 派生和 store 生命周期约束，补齐父 Agent、子代理、双 Provider 和真实本地 CLI 消费者验证。

## Capabilities

### New Capabilities

- `canonical-tool-result-boundary`: 定义所有工具公共 16 MiB（16,777,216）canonical JSON 字节上限、canonical 完整脱敏结果、尺寸与 redaction 解耦、超限错误和历史引用兼容性。
- `read-file-pagination`: 定义 `read_file` 的 0-based 行 `offset`、正数 `limit`、行号、边界错误和无参数完整读取行为。
- `tool-result-archive-projection`: 定义 Provider first-use/capacity/stale 投影、ArchiveRead 消费闭环、父子 capability、终端分层和生命周期失败保护。

### Modified Capabilities

无。当前 `openspec/specs/` 没有已登记的主 capability 规格；本 change 的 3 个 capability 将先以增量 spec 形式建立行为合同。

## Impact

- 影响 `src/rollo/tools.py`、`runtime_lifecycle.py`、`redaction.py`、`event_sink.py`、Provider projection、ArchiveRead capability、`agent.py`、`subagent.py`、终端投影及相关测试。
- 影响 Anthropic 和 OpenAI-compatible Provider 的工具结果消息，以及公共工具结果适配路径；不改变副作用工具的 dispatch、权限、retry、recovery 或 outcome-unknown 语义。
- 需要复用现有 Canonical Runtime Event、ArtifactArchive、session/lineage scope 和模型 replay；不新增外部依赖，不迁移或删除历史 artifact/session 数据。
- 参考 `D:/workspace/maka@57e08d83497d1d7ace7d6eff88e4e5267a0345b5` 的已核对源码语义，但不修改 Maka；Maka 的可选 `maxResultBytes` 和 8 MiB transport cap 作为证据边界，不作为本项目 16 MiB 公共上限的替代定义。
