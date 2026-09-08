## 1. 契约与测试基座

- [x] 1.1 固化公共结果规范化、canonical JSON UTF-8 字节计数、`result_too_large` 错误形状、16 MiB（16,777,216 字节）边界值和 `read_file` 分页提示的契约测试与实现说明。
- [x] 1.2 增加超过 16 KiB、超过 8 KiB、包含嵌套普通字符串、secret marker 以及重复经过 `_outcome()`/`CanonicalSink` 的 canonical fixture。
- [x] 1.3 扩展本地 Anthropic 与 OpenAI-compatible fake consumer，捕获最终序列化 Provider 请求，并区分 canonical、Provider 和终端视图。

## 2. 公共工具结果边界与 canonical 保留

- [x] 2.1 在内置工具、MCP、子代理结果和其他工具适配入口实现统一的公共结果规范化/限长 gate，移除各工具的静默首尾截断，采用 16 MiB（16,777,216）canonical JSON 字节公共上限。
- [x] 2.2 为所有公共工具路径实现有界且安全的 `result_too_large` 错误；`read_file` 错误包含 offset/limit 提示，且不得泄漏 secret、traceback、archive 根路径或其他 session 数据。
- [x] 2.3 移除 `DurableToolBoundary.max_result_bytes=16_384` 对立即归档、placeholder 替换和普通结果截断的语义；如现有调用方需要则仅保留兼容参数。
- [x] 2.4 让 canonical tool-result redaction 具备路径感知能力：继续脱敏 secret/path，同时完整保留普通大字符串，不生成 `inline:*` 或不可读 `bounded_ref`。
- [x] 2.5 确保 `_outcome()` 与 `CanonicalSink` 产生一致的完整安全结果、digest 输入和错误状态，消除 double-bounding，并保持历史 `bounded_ref` replay 兼容。
- [x] 2.6 增加 canonical 回归测试：首次 Provider 请求能容纳时不写 archive，后续 projection 不修改 canonical event。

## 3. read_file 按行分页

- [x] 3.1 为 `read_file` schema 增加可选 `offset`、`limit`，并保持 Anthropic/OpenAI-compatible tool definition 与本地 dispatch 的契约一致。
- [x] 3.2 实现严格参数校验：offset 为非负整数、limit 为正整数，拒绝 bool/float/字符串隐式转换，默认 offset 为 0，缺省 limit 读取到文件末尾。
- [x] 3.3 在添加现有 1-based 原文件行号前切分并截取文件行，固定 LF/CRLF、空文件、EOF、Unicode 和无末尾换行行为。
- [x] 3.4 保留成功读取时的 mtime/先读后改状态；非法分页参数必须在文件访问、状态变更和 archive 写入前返回。
- [x] 3.5 对最终带行号的分页结果应用 16 MiB（16,777,216）canonical JSON 字节公共上限；页过大时返回受控的小 limit 错误，不做首尾混合。
- [x] 3.6 增加 read_file 回归测试：无参数、offset/limit 示例、EOF、空页、非法输入、Unicode、LF/CRLF、16,777,215/16,777,216/16,777,217，以及与 ArchiveRead 的单位区分。

## 4. Provider 中立的 first-use、容量和 stale 投影

- [x] 4.1 扩展中立 archive-aware projector，同时接受完整 canonical tool result 和历史 `bounded_ref`，并保证不修改 canonical replay 输入。
- [x] 4.2 将 first-use 定义为首次包含该工具结果的 Provider request，并使用 provider-specific 最终序列化 payload 与 Mini 当前有效窗口预算做 fit 判断。
- [x] 4.3 实现 fit 分支：发送完整安全结果，不归档、不生成 ref/placeholder、不隐式 hydrate。
- [x] 4.4 实现容量救援的 projection-time archive 创建与内容寻址复用，生成最多 4,000 字符前缀、`truncated`、实际 `next_offset`、ref 和 ArchiveRead 指引。
- [x] 4.5 实现 preview 缩短与最小可行动 fallback；rescue envelope 无法容纳时继续降级，无法建立可读 ref 时返回受控错误。
- [x] 4.6 实现 stale projection：只有相关后续 step/context event 发生后才把旧结果变成 archive placeholder，并兼容历史 placeholder 和默认读取指引。
- [x] 4.7 将同一中立 projection 接入 Anthropic/OpenAI-compatible 的 live tool loop、cold replay、resume 和 compaction 重建。
- [x] 4.8 增加双 Provider fake-consumer 测试：首次 fit、容量救援、stale 转换、canonical 不改写、实际续读 offset 和 archive failure。

## 5. ArchiveRead capability、父子 scope 与生命周期

- [x] 5.1 扩展现有 `ToolResultArchiveCapability`/archive adapter 的 projection-time 写入或复用能力，同时保留 ref、metadata、hash、size、session、lineage、scope 和 decoder 校验。
- [x] 5.2 保持 ArchiveRead 由 capability 派生且唯一；测试 inspect、文本字符页、二进制字节页、默认 6,000、最大 7,500，以及拒绝 file path 和全局 digest 查找。
- [x] 5.3 固化 ArchiveRead 对 missing ref、invalid range、limit overflow、scope/session mismatch、integrity mismatch、decoder failure 和 store closed 的有界稳定错误码/消息。
- [x] 5.4 向 skill-fork 和子 Agent 传递只读派生 archive capability，不扩大普通 child allowlist，并验证父 Agent 可读取的子结果不会再次变成 opaque bounded_ref。
- [x] 5.5 增加父先结束、子先结束、取消、pending read 和 caller-owned store 测试，证明子代理完成不会关闭或替换父 Agent 的 archive/runtime 资源。
- [x] 5.6 增加 close barrier：Agent-owned store 关闭前等待 pending projection、archive write/read 和 ArchiveRead；关闭后读取必须 fail closed，不得隐式 reopen。

## 6. 终端投影

- [x] 6.1 将 inline、capacity preview、stale placeholder、ArchiveRead page 和 error 的终端格式化与 Provider projection、canonical 存储分离。
- [x] 6.2 确保终端显示安全正文或可行动的有界指引，不把 metadata JSON 当作文件正文，也不因可见 ref 而隐式 hydrate 全部 archive。
- [x] 6.3 增加终端/CLI 测试：redaction、有界 preview、ref/hint 展示、ArchiveRead 页、archive error、store-closed 状态和 Provider 消息不变。

## 7. 分层验证与回写

- [x] 7.1 运行公共 canonical JSON 字节上限、canonical integrity、redaction、read_file 分页、Provider projection、ArchiveRead、父子 scope 和生命周期故障的 focused unit/integration 测试。
- [x] 7.2 使用真实本地 CLI 和隔离 session 完成大文件读取、首次 Provider 请求捕获、必要的 ArchiveRead 分页、继续对话和 session 保存；将真实消费者证据与 fixture/fake 证据分开记录。
- [x] 7.3 运行完整 Python 测试、OpenSpec 校验和 `git diff --check`；审查受保护最终 diff，确认既有 `agent.py` 用户修改保持不变。
- [x] 7.4 验证未修改 `D:/workspace/maka`、未迁移历史 artifact/session，且未执行任何交付动作。
- [x] 7.5 在实现验收后，将命令、证据等级、残余风险、Gate 状态和工作树处置回写 canonical task source 与 ISS002。

## 8. Maka active-step 与 ArchiveRead 递归回归修正

- [x] 8.1 修正 neutral projector 的 `first-use` 判定：只选择最后一个连续完整 assistant/tool 组，避免把同一用户轮次的多个组聚合比较；增加多组 replay 回归。
- [x] 8.2 更新 live Provider loop 合同：archive-aware 模式在 refresh 前跳过旧 Tier 1/2/3 工具结果截断、snip、microcompact 及其 lightweight replacement，保留完整 compaction 独立路径；legacy 压缩入口不得被 live loop 旁路调用。
- [x] 8.3 在 neutral projection 中按 `tool_call_id` 识别 `ArchiveRead`，将 page/metadata/query/error 作为已经有界的结果直接投影；历史 `ArchiveRead` bounded ref 只能复用原 ref，禁止再次调用 archive writer。
- [x] 8.4 为 Anthropic/OpenAI-compatible 增加 ArchiveRead 正常 fit、aggregate capacity 不足、历史 bounded ref、重复 projection 和 live 高利用率/重试回归，断言正文/错误可消费、artifact 数量不增加、ref 不递归、`size_bytes` 不膨胀，并补齐任务源、ISS002 和本 change 的验证证据。
