## Context

ISS002 要求修正当前“工具执行结果 → durable boundary → Canonical Runtime Event → Provider/终端投影”的边界。目标仓库当前工作树已有 `src/rollo/agent.py` 未提交修改，本 change 不覆盖或重置该差异，也不修改 `D:/workspace/maka`。

已核对的实现事实如下：

- `src/rollo/tools.py` 的 `MAX_RESULT_CHARS = 50000` 同时影响 `read_file` 和通用 handler 路径，因此不是 read_file 单点限制。
- `DurableToolBoundary._bound_result()` 以默认 16 KiB 为界立即写入 `ArtifactArchive` 并把 placeholder 写入 outcome；`_outcome()` 和 `CanonicalSink` 还会执行 redaction。
- `redact_payload()` 对普通递归字符串的 `max_string_chars=8192` 会生成 `ref=inline:*` 的 `bounded_ref`；该 ref 不保证对应真实 ArtifactArchive 内容。
- `archive_capability.py`、`archive_projection.py` 和 Provider context adapter 已存在，能够消费已有 artifact ref，但当前 projection 主要从已有 `bounded_ref` 开始，不能从完整 canonical tool result 按需建立归档。
- `agent.py` 已按模型上下文窗口的 70% 形成本地有效预算，并可以用最终 Provider 序列化结果测量候选消息；该 70% 是 Mini 的本地策略，不是 Maka 的公共协议。

因此本 change 不是重新设计整个 ArtifactArchive，而是把公共工具结果安全上限、canonical 保存语义和 Provider 按需归档语义拆开，并补齐 `read_file` 分页及真实消费者验证。

## Goals / Non-Goals

**Goals:**

- 为所有公共工具结果建立统一的 16 MiB（16,777,216）canonical JSON UTF-8 字节安全上限；超过上限返回可诊断错误，不再静默首尾截断。
- 在不超过公共上限的前提下，让 canonical 保存完整脱敏 tool result；16 KiB boundary 和 8 KiB redaction limit 不得把普通结果变成不可读 placeholder。
- 仅在 Provider projection 确实需要容量救援或 stale 归档时写入 ArtifactArchive；首次包含结果的请求能容纳时不主动归档。
- 为 `read_file` 增加按行的 `offset` / `limit`，并维持原文件 1-based 行号展示和先读后改状态。
- 复用并校正现有 ToolResultArchiveCapability、ArchiveRead、父子 capability 派生、双 Provider adapter、终端投影和生命周期保护。
- 用自动化测试和真实本地 CLI/隔离 session 验证 canonical、Provider、ArchiveRead、父子 Agent 和 store 生命周期的边界。

**Non-Goals:**

- 不迁移、删除或重写历史 canonical event、artifact、session 或 runtime store 数据。
- 不修改 Maka，不移植 Maka 的完整 runtime/graph/UI，也不把 Maka 的可选 `maxResultBytes` 或 8 MiB transport cap 当作本项目规则。
- 不取消 secret/path redaction、session/lineage scope、hash/size 校验、ArchiveRead response cap 或 fail-closed 生命周期。
- 不改变副作用工具的 dispatch、权限确认、outcome-unknown、retry 或 recovery 语义。
- 不把调高公共结果上限当成 Provider 上下文容量方案；Provider/history projection 仍保留独立 token/byte budget。

## Decisions

### 1. 在公共工具结果入口统一执行 16 MiB（16,777,216）canonical JSON 字节上限

新增或收敛一个公共结果规范化/限长入口，覆盖内置工具、MCP tool adapter、子代理对外返回和其他进入 runtime boundary 的工具结果。Agent 主路径将 adapter 的 raw value 交给 DurableToolBoundary，由该边界完成唯一一次规范化/计数；独立 `execute_tool`/MCP public wrapper 仍可对外返回文本，但不能在 Agent 路径中二次包装同一结果。禁止各工具保留不同的静默首尾截断实现。

计数口径冻结为：

- `bytes` 先转为 JSON-safe 的 Base64 binary envelope；
- 字符串和结构化结果都按项目 canonical JSON 规则生成稳定 JSON，再计算 UTF-8 字节数；字符串的引号和转义计入；
- 计数发生在 handler/MCP/child adapter 完成最终公共结果规范化之后、canonical redaction 和 Provider projection 之前；
- `16,777,215` 和 `16,777,216` 字节允许，`16,777,217` 字节拒绝；实际错误 envelope 本身必须受同一上限约束。

超限统一返回结构化的 `result_too_large` 工具错误，至少包含工具名、公共上限和安全的实际计数/诊断信息，不包含 secret、traceback 或本地归档根路径。`read_file` 额外提示使用较小的 `offset` / `limit`；其他工具不在本 change 中臆造分页 API。

不采用继续调用 `_truncate_result()` 的方案，因为它会把事实内容静默改成首尾混合结果；也不采用仅修改 `MAX_RESULT_CHARS` 的方案，因为 MCP、子代理或其他适配旁路可能绕过该常量。

### 2. Canonical tool result 只做隐私脱敏，不做尺寸 placeholder

`DurableToolBoundary` 不再用 `max_result_bytes=16_384` 决定 archive、placeholder 或 canonical result 形状。该参数如果为兼容调用保留，不得再触发立即归档/截断；公共 16 MiB（16,777,216）上限是工具结果能否进入 canonical 的前置安全门。

`_outcome()` 和 `CanonicalSink` 必须共用明确的 canonical tool-result redaction 语义：

- `function_response.result` 内的 secret、敏感 key 对应值、敏感 marker 和受保护路径继续脱敏；
- 普通大字符串以及结构化结果中的普通大字符串完整保留，不生成 `ref=inline:*`、`bounded_ref` 或首尾截断文本；
- 事件其他 metadata 仍可使用既有 `max_string_chars` 保护，但不能通过递归 redaction 重新处理并裁剪 tool result；
- `tool_outcome` 与 `function_response` 两个 canonical event 的安全结果、digest 和错误状态必须一致；
- 任何 canonical preparation 二次执行都必须保持上述结果，不得出现第一次完整、第二次 inline ref 的 double-bounding。

选择路径感知 redaction/显式 tool-result redaction，而不是全局关闭 redaction，是为了同时满足 canonical 完整性和隐私约束。canonical 保存的是完整脱敏事实，不是 raw secret，也不承诺保存超过公共上限的结果。

### 3. Provider projection 按最终请求容量按需归档

扩展现有中立 Provider projector，使其同时识别完整 canonical tool result 和历史 `bounded_ref`。projector 对输入消息做副本，不能回写 canonical event。

对首次包含某个 tool result 的 Provider request，按以下顺序处理：

1. 根据 canonical event 顺序、tool call id 和最新 completed step 判断该结果是否仍是 first-use 可消费结果；这里的“首次请求”指首次包含该工具结果的 Provider request，不是用户输入轮次。
2. 使用当前 Provider 的最终序列化消息、系统提示、工具定义和其他消息计算候选请求容量。当前 Mini 的 `model_context_window * 70%` 继续作为本地有效窗口启发式；最终候选必须经过 provider-specific formatter 的 `size_fn` 检查。
3. 如果完整安全结果可容纳，直接投影完整结果，不写 ArtifactArchive、不生成 ref。
4. 如果完整结果不能容纳，调用当前 capability 绑定的 archive writer 写入完整安全结果，并在写入成功、ref 可被当前 capability 读取后，生成前缀 preview、`truncated`、ref、实际 `next_offset` 和 ArchiveRead 指引。
5. 如果 capacity-rescue envelope 仍不能容纳，逐步缩短 preview；最后保留最小可行动 ref、截断状态和 ArchiveRead 指引。若无法建立有效 ref，则返回受控错误，不发送悬空或裸不可读 `bounded_ref`。
6. 如果结果已经 stale，使用 archive placeholder；若 canonical 结果尚未归档，则在 stale projection 此时按需归档，不重新把旧全文注入 Provider。

默认参数沿用当前能力模块的安全值：preview 4,000 字符，ArchiveRead 默认页 6,000，最大页 7,500。它们是 Mini 的本地契约，不声称是 Maka 的公共 API；ArchiveRead 文本页使用字符单位，二进制页使用字节单位。

归档写入需要内容寻址或等价去重，避免同一 canonical result 在多次 projection 中产生不可控的重复 artifact。写入失败、metadata commit 失败、read-back/hash 校验失败或 capability 不可用时，不得发布成功 ref；若原始安全结果无法放入当前请求，则返回受控错误并保留 canonical 事实。

### 4. `read_file` 按行分页，工具公共上限作用于最终页结果

`read_file` schema 增加可选 `offset` 和 `limit`：

- `offset` 为非负整数，表示 0-based 起始行；默认 0；
- `limit` 为正整数，表示最多返回的行数；缺省时读取到文件末尾；
- 校验必须拒绝 bool、负数、零 limit、浮点、无法解析的字符串和其他隐式纠正输入；
- 读取文件后先按现有换行语义切行，再按 offset/limit 切片，最后添加当前格式的 1-based 原文件行号；
- offset 超过末尾返回确定的空结果；空文件、最后无换行、LF/CRLF 和 Unicode 行为由测试冻结；
- 最终带行号的公共工具结果仍经过统一 16 MiB（16,777,216）canonical JSON 字节上限；超限返回分页提示错误，不做头尾拼接。

文件 mtime/先读后改状态沿用现有语义：只有文件读取成功且未产生参数错误时更新对应路径状态。`read_file` 的行 offset/limit 与 ArchiveRead 的字符/字节 offset/limit 必须在工具描述和实现中显式区分。

### 5. 复用现有 capability，保持 ArchiveRead 和父子 scope 闭环

不新建全局无权限的 ArchiveRead 工具。继续使用 `ToolResultArchiveCapability` 绑定 archive、decoder、session、lineage、allowed runs 和 bounded page limits；Provider tool definition 仅在 capability 有效时派生。

父 Agent 向子代理传递只读派生 capability，普通 child allowlist 保持不变；子代理不能关闭或替换 caller-owned archive/store。父子返回路径必须在父 capability 仍有效时完成 Provider projection。ArchiveRead 继续执行 ref 格式、metadata、hash、size、session/lineage、scope、page limit 和 store closed 校验。

历史 `bounded_ref` 仍可由兼容 projection 读取；缺少 read instructions 时根据经过校验的 metadata 生成默认指引，但不重写历史事件。ArchiveRead 不接受 file_path、不重新归档、不执行隐式全文 hydrate。

### 6. 终端、Provider 和 canonical 保持三种视图

canonical 是完整脱敏事实；Provider 是按 first-use/capacity/stale 状态生成的请求视图；terminal 是人类可读的有界视图。终端可以显示小结果正文、容量救援 preview、stale 状态和 ArchiveRead 指引，但不能因为看到 ref 就隐式读取全文，也不能把 metadata JSON 伪装成正文。三种视图必须共享 redaction、ref 和错误安全规则，但不能相互回写。

### 7. 生命周期与失败处理采用 fail-closed

projection-time archive 与 ArchiveRead 都依赖 runtime capability 生命周期。Agent/child 的 pending provider projection、archive write/read 和 ArchiveRead task 在 Agent-owned store 关闭前完成；caller-owned store 由 caller 管理。store 关闭、scope 不匹配、ref 不存在、metadata/hash/size 不一致和 decoder 不可用时返回稳定受控错误，不 reopen 另一个 store，不生成可成功消费但实际不可读的 ref。

### 8. 测试按证据层级实现

自动化测试分为：公共结果上限与 redaction 单元、canonical event integrity、Provider neutral projection、Anthropic/OpenAI final request fake consumer、ArchiveRead capability、parent/child lifecycle、terminal/CLI 和既有回归。测试必须覆盖 16 KiB/8 KiB 旧边界、16,777,215/16,777,216/16,777,217 公共上限、首次 fit、capacity rescue、stale、archive failure、store close、Unicode 和双 bounding。

真实本地验证使用隔离 session 和实际 CLI，执行“读取大文件 → 捕获首次 Provider 请求 → 必要时调用 ArchiveRead → 继续对话/退出保存”；fixture、mock、内部 projector 返回值只能作为辅助证据。

### 9. 最新 completed step 在首次消费前受保护

Maka 的 active prune 明确排除最新 completed provider step，给模型一次请求消费精确工具证据的机会。Mini 的旧 Tier 1/2/3 管线按利用率或空闲时间直接改写 provider-visible 工具结果，并且可能把 replacement 持久化为 context transition；它在 live loop 的 Provider refresh 之前执行，因此即使 archive projection 本身支持 first-use，也无法保证模型先看到完整结果。

因此 live Anthropic/OpenAI-compatible loop 调用压缩入口时必须进入 `archive-aware` 模式：不执行旧的工具结果 budget 截断、stale snip 和 microcompact 变换，也不为这些变换写入 `lightweight_compression` replacement。下一次 Provider context refresh 直接从 canonical replay 读取完整结果，再由中立 archive projection 判断 first-use、容量救援或 stale。旧压缩方法保留为显式 legacy/安全测试入口，不能成为 archive-aware live loop 的旁路。

这不是取消上下文压缩：完整 compaction 仍由既有 `_check_and_compact()` 和 canonical context transition 负责；本决策只移除与 archive-aware tool-result projection 冲突的旧工具结果改写路径。

### 10. ArchiveRead 结果是有界终端，不得递归归档

`ArchiveRead` 的 `read`、`inspect`、`query` 和稳定错误 envelope 已由 capability 限制大小，属于模型消费 archive 的控制结果。projection 必须通过 assistant tool call 的 `tool_call_id`（必要时回退中立消息上的工具名）识别该结果，并在任何通用 `archive_result()` 之前单独处理：

- 页面/metadata/error 在最终 Provider payload 能容纳时原样保留，正文仍可被模型直接消费；
- 已存在的历史 `bounded_ref` 可以按原 ref 做兼容读取/投影，但不得因为它来自 `ArchiveRead` 再创建新 artifact；
- 如果有界页面连当前 aggregate payload 都放不下，返回带原 ref/重试较小 limit 提示的 `capacity_exhausted`，不截取页面后再归档，也不生成 ref 套 ref。

这样 `ArchiveRead` 是归档的读取闭环而不是归档输入，重复 projection 的 artifact 数量、ref 深度和 `size_bytes` 不会因页面 envelope 逐层膨胀。

### 11. first-use 只选择最后一个连续完成的 Provider step

一次用户输入可能触发多个 assistant/tool Provider step。canonical replay 会按事件顺序保留这些组，但不能把用户边界之后的全部 tool result 合并，再拿第一组 assistant 的 `tool_calls` 做集合比较；这种比较在第二组完成后会得到空的 `first-use` 集合，从而把最新工具结果也立即降级为 stale placeholder。

neutral projector 应从最后一个 tool result 向前收集同一连续 tool 组，找到其直接对应的 assistant tool-call 消息，并且只在两边 call id 集合完全一致时把该组标记为 first-use。若最后一个 tool result 后出现普通用户/assistant 内容，则该组不再是 first-use；代码注入的 context user message 可以继续作为非语义边界忽略。这样每个新完成组在第一次 Provider request 前保持完整，下一组完成后前一组才变 stale。

## Risks / Trade-offs

- [canonical 事件体积增加] → 16 MiB（16,777,216）canonical JSON 字节公共上限限制单次事实增长；保留 session/compaction/Provider 独立预算，不把 canonical 大小与 Provider inline 大小混为一谈。
- [每次容量救援或 stale projection 可能触发 archive 写入] → 使用内容寻址/已有 ref 复用，并在写入成功且可读后才生成 placeholder。
- [字符预算与真实 token 计费存在误差] → 以最终 Provider 序列化 `size_fn` 做本地 fail-safe；预览 envelope 仍有最小可行动 fallback。
- [redaction 二次执行造成再次 bounding] → 为 `content.result` 建立路径感知 canonical redaction，并加入 `_outcome()`/CanonicalSink 双边界回归。
- [MCP 或子代理旁路公共上限] → 把上限放在公共工具结果 ingress，测试内置、MCP、child result 和未知适配分支，而不是只断言一个常量。
- [旧 placeholder 缺少读取指引] → 兼容 projection 从已校验 ref metadata 补充默认 ArchiveRead hint，不迁移历史数据。
- [父子 close 竞态] → capability 派生不转移 ownership，关闭前等待 pending tasks，并分别测试父先结束、子先结束和取消路径。
- [旧压缩入口绕过 archive projection] → archive-aware live loop 显式跳过 Tier 1/2/3 工具结果改写，并用双 Provider 的“首次请求完整可见”回归锁定；legacy 入口保留但不由 live loop 调用。
- [ArchiveRead 页面递归归档] → 以 tool call 名称做非递归分流，紧预算时返回有界容量错误，并验证重复 projection 不产生新 artifact。

## Migration Plan

1. 先落盘并校验本 change 的 proposal、3 个 delta spec、design 和 tasks；实现前冻结公共上限、redaction、分页、projection 和错误合同。
2. 先补公共结果上限、canonical redaction 和 canonical integrity 测试，再移除 durable boundary 的立即归档/截断语义；旧 ref 读取保持兼容。
3. 扩展 neutral Provider projector，从完整 canonical result 支持 first-use fit、capacity rescue、stale 按需归档，并接入两个 Provider。
4. 接入 `read_file` 行分页、公共超限错误、ArchiveRead 单位说明、父子 capability 和终端投影。
5. 运行 focused/full tests、OpenSpec strict、git diff --check 和真实本地 CLI 隔离 session 验证；将实际证据回写任务源和 ISS002。
6. 针对 live first-use 和 ArchiveRead 递归回归补充 OpenSpec 任务、实现和双 Provider/真实本地验证；回滚只回滚本 change 的代码/测试/OpenSpec 文件，不删除既有 artifact、canonical event 或 runtime store；delivery、push、MR、部署另行授权。

## Open Questions

无阻断性开放问题。本 change 将以下决策交给 spec 固化：公共上限为所有工具共用的 16 MiB（16,777,216）canonical JSON UTF-8 字节；超限统一受控错误；`read_file` 使用 0-based 行 offset/正数 limit；ArchiveRead 使用字符/字节页单位；first-use 以首次包含该结果的 Provider request 定义；projection 失败不得生成悬空 ref。
