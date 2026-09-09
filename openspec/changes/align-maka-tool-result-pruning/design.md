## Context

当前 CanonicalModelContextAdapter 每次从 canonical replay 构建 Provider context，并由 project_archived_tool_results() 决定工具结果是全文、preview 还是 placeholder。现有实现已经具备首次请求 fit、稳定 archive ref、ArchiveRead 指引和最终容量 gate，但 stale 判定仍主要依赖“最新完整 assistant/tool group”：它没有 Maka 的 turn/step 规模阈值，且归档失败时会把 raw 结果替换成错误 envelope。

Maka 将工具结果裁剪拆成两条独立路径：历史 turn 使用 staleToolResultPrune，当前 turn 的已完成 step 使用 activeToolResultPrune；整体容量由独立的 request-projection/final-request gate 负责。本项目采用用户确认的 ceil(序列化结果字符数 / 3) 作为裁剪估算，保留现有 Provider context-bearing request envelope 的 UTF-8 字节预算和公共 16 MiB canonical result boundary。/3 是本项目的启发式，不是对 Maka 默认 /4 的机械复制。

canonical event 仍是完整工具结果和生命周期事实的唯一来源。Provider-visible placeholder、preview、supersession 和 projection diagnostics 都是只读投影，不写回 canonical event；已建立的 placeholder 必须保持稳定，以保护跨轮次 Provider 前缀缓存。

## Goals / Non-Goals

**Goals:**

- 使用唯一的 canonical serialization 入口计算 ceil(chars / 3)，明确 2048 ordinary prune threshold 和 256 supersession threshold。
- 按 canonical ordinal 的首次出现顺序识别最近 2 个受保护 turn，并实现 Maka 风格 stale prune。
- 按当前 turn 的 completed step 实现 active prune：较早 step 超过 2048 才归档，最新 completed step 在仍是最新时默认保留。
- 在首次完整 Provider envelope 不 fit 时，显式执行最多一次 emergency active pass，让最新 step 也有机会按阈值归档。
- 使用 replay sidecar metadata 统一 cold/incremental 分类，确保内部字段不进入 Anthropic/OpenAI wire。
- 让归档失败 fail-open：保留 raw，向 ProviderContext.diagnostics 暴露有界诊断，不生成不可恢复的成功 placeholder。
- 对可证明的当前 turn active-step 重复结果、read_file 覆盖范围和受支持 snapshot 提供保守 supersession。
- 保持 archive ref、placeholder、ArchiveRead continuation、recovery rebuild 和重复投影幂等稳定。
- 保留明确字段集合的 Provider context envelope 最终字节 fit、SDK dispatch gate 和 16 MiB canonical result boundary。
- 明确 request-cycle owner/state、缺失 identity 的逐字段降级和 My 工具到 Maka 只读语义的映射。

**Non-Goals:**

- 不修改 D:/workspace/maka，不引入 tokenizer 或外部依赖。
- 不修改 canonical SQLite schema、RuntimeEvent 字段、工具结果正文或 16 MiB 公共上限。
- 不把 /3 用于 Provider 计费 token、模型上下文窗口或最终 request 字节预算。
- 不对缺少可靠 identity 的工具或并行 step 做猜测性语义合并。
- 不将 stale/active placeholder 写回 canonical event，不迁移历史 artifact。
- 本 change 不实现 Maka 的 failure_resolved supersession；如需支持，应另开 change。
- 不执行 commit、push、MR、merge、release、deployment 或真实外部 Provider 验证。

## Decisions

### D1：复用 canonical serialization，单独建立 prune estimator

裁剪估算必须复用现有 tool_result.py 的 canonical_tool_result_bytes(value, tool_name=...)。先得到规范化后的 JSON UTF-8 bytes，再 decode 为确定性的 Unicode 序列化文本，计算：

~~~text
estimated_tokens = ceil(len(serialized_text) / 3)
~~~

该序列化已包含 JSON 引号、转义、对象结构及 bytes 的 Base64 canonical envelope。serialized_text 的长度定义为 Python Unicode code-point 数量，不是 UTF-8 byte 数量，也不是 terminal display 文本长度。普通 stale/active 裁剪条件为 estimated_tokens > 2048；active semantic supersession 条件为 estimated_tokens >= 256。不可序列化时不得把非 canonical 的展示文本用于估算，保留 raw 并产生 projection diagnostic。

选择这一入口而不是 _measurement_text()，是为了避免 projection、archive capability 和 public boundary 使用三套不同的正文表示。选择 /3 而不是 Maka 默认 /4 或 Provider UTF-8 bytes，是本项目已确认的策略取舍：它只决定“是否值得裁剪”，不决定请求是否能发送。

**替代方案：**直接按 UTF-8 bytes/4 估算会混淆 Provider capacity 与 prune heuristic；继续使用各入口独立字符计算会破坏边界一致性，均不采用。

### D2：使用 sidecar replay metadata，而不是扩展 Provider message

ModelReplayResult 和 incremental cursor 增加与 neutral message 一一对齐的不可变 ReplayMessageMeta sidecar。每条相关消息的 metadata 至少包含 runtime_event_id、canonical ordinal、turn_id、run_id、invocation_id、step_key、step completion/partial 状态、tool_call_id、tool_name、canonical arguments digest、必要的 read_file range identity 和 RuntimeEvent terminal status。

字段来源以 RuntimeEvent 为准，而不是从 Provider-specific message shape 猜测。当前 Agent 的 RunContext 已包含 turn_id、run_id 和 invocation_id，生产路径显式传入 active_turn_id。sidecar 还携带 identity_state，取值为 complete、partial 或 synthetic，用于按字段缺失安全降级：

- complete：拥有 event id、ordinal、turn/run/invocation、tool call/name、body identity 和 step status，可执行 stale、active、稳定 archive reuse 和 active supersession；
- partial：若拥有 turn 与 ordinal，可执行 stale，但缺少 run/invocation 或 step status 时禁止 active；缺少 event id、tool call/name 或 body identity 时禁止新 archive ref reuse 和 semantic supersession；
- synthetic：compaction reset 注入的 context message 或无 canonical event 来源的 neutral message，不参与 stale、active、supersession 或新 archive，只允许既有合法 placeholder/ArchiveRead envelope 透传。

缺少 sidecar 的直接 neutral caller按 synthetic 处理，不从 message 字段猜测 active step。缺失任一字段时必须输出稳定 diagnostic code，例如 replay_metadata_unavailable、archive_identity_unavailable 或 synthetic_message_unclassifiable；不得用同一个 reset event id 把多个 synthetic message 当成真实 tool event。

sidecar 不参与 neutral message digest，也不序列化进 Provider request。Anthropic/OpenAI 转换层采用显式 allowlist，只复制 Provider 合法字段；内部 turn_id、run_id、invocation_id、step_key、ordinal、identity_state 和 metadata wrapper 均不得透传。compaction reset、reopen 和 incremental rebuild 必须同步清理/重建 sidecar，并比较 sidecar 而不仅是可见 messages。

**替代方案：**把 metadata 直接塞进 neutral message 再由 _without_runtime_id() 逐字段剥离风险较高，当前函数只剥离少数字段，容易发生 wire leakage；不采用。

### D2.1：在 canonical 边界规范化工具参数，并兼容旧字符串事件

Provider 返回的 OpenAI `function.arguments` 通常是 JSON object string。新写入的 `ModelCallRecorder.final_tool_call()` 必须复用 `decode_tool_arguments()`：成功解码且结果为 object 时，以 redacted mapping 写入 canonical final-call event；Anthropic 已是 mapping 的路径保持相同规范化结果。这样 `RuntimeEventEmitter` 的 final-call 去重和后续 replay 使用同一种 canonical input。

解码失败时不得把参数替换成 `{}`，必须保留原始安全值，并由 replay sidecar 将 `semantic_input_complete` 置为 false；该调用不能参与 read range、Glob/Grep snapshot 或 semantic duplicate supersession。replay metadata 仍需在 `_call_arguments()` 再次调用同一 decoder，以兼容修复前已经保存的 JSON 字符串 canonical event。参数 digest 可以继续对保留值计算，但不能把 digest 存在误当作 descriptor 完整。

该策略只改变后续新写入的 canonical final-call 参数表示，不迁移历史 event；历史 string event 在内存 replay 时转换为 mapping，不改变 canonical bytes。非法 JSON 的原文只留在 canonical/诊断可见范围，不进入 semantic descriptor 或错误 envelope。

**替代方案：**只修 Provider replay message、让 canonical event 继续在每次请求中保留不同表示，会使 event dedup 与 sidecar 的 input identity 分叉；把非法 JSON 静默转为空 object 会制造错误的 semantic supersession；均不采用。

### D3：先按 Maka 规则分类，再执行两条独立裁剪路径

投影对每个 tool response 按以下优先级处理：

1. 合法 archive placeholder、ArchiveRead page、bounded ref 或 ArchiveRead error envelope：先做结构/授权校验，直接复用或透传，禁止重新归档。
2. 当前 active_turn_id 内的 completed step：较早 completed step 进入 active 候选；当前 turn 中 canonical ordinal 最大的 completed step 保持最新，默认不进入候选。
3. 不是最近 2 个 turn 的 raw result：进入 stale 候选。
4. 其他 raw result 保持完整，不因为 aggregate messages budget 变化而主动改写。

普通候选只有 estimated_tokens > 2048 才能归档。semantic supersession 只在 active current-turn steps 中生效，并且需要 estimated_tokens >= 256 与确定的 newer-step identity proof；它不改变 stale 路径的阈值。并行调用、未知工具、参数不完整、失败覆盖成功和无法确定范围时均保持原文。本 change 不实现 Maka 的 failure_resolved，避免把失败状态迁移扩大成新的语义契约。

最近 2 个 turn 按 canonical ordinal 中 turn 首次出现的顺序确定，不按 turn_id 字符串排序。若历史消息缺少 runtime_event_id、tool call identity 或必要的 tool name，不能声明稳定跨结果复用或 semantic supersession；无法安全归档时保持 raw 并记录诊断。

### D3.1：采用显式的 My-Claude-Code 工具语义映射

参考 Maka active-tool-result-working-set 的 Read/Glob/Grep/Bash descriptor，但只复制其可证明的 identity 规则，不复制工具名称或副作用假设：

- read_file 对应 Read：输入 identity 为规范化 file_path、offset（缺省 0）和 limit（缺省为正无穷）；路径只移除显式的前导 ./，不折叠反斜杠、UNC 前缀或重复斜杠。只有同一路径且 newer range 覆盖旧 range 的成功结果才可 supersede。
- list_files 对应 Glob：输入 identity 为 pattern 和规范化 path/cwd；只对同一成功 snapshot key 的严格更新 step 做 newer_snapshot supersession。
- grep_search 对应 Grep：输入 identity 为 pattern、规范化 path 和 include；只对同一成功 snapshot key 的严格更新 step 做 newer_snapshot supersession。
- run_shell 对应 Bash：默认不做 snapshot supersession；仅当命令是前台、无 shell 元字符/命令替换的只读 git status、diff、log、show、branch 或 rev-parse 查询时，才允许按规范化命令建立 snapshot key。其他 shell 结果只可参与带相同工具名、canonical input、成功/失败状态和 body hash 的 exact duplicate。

exact duplicate key 按 Maka 规则由 semantic tool name、canonical input、success/error 状态和 body SHA-256 组成，不包含 tool_call_id；只有 newer step 且同一 active turn 内才可替换。当前 change 明确不支持 failure_resolved，失败结果不能被成功结果抹除；并行 step、不同 turn 和相同输入但不同 body 均不触发 supersession。

**替代方案：**继续把“最新完整 tool group”当作唯一 active group，会重现当前 turn 早期结果立即归档的问题；每次依据整个 messages 重新把 placeholder 改成 capacity error，则破坏前缀稳定性；均不采用。

### D3.2：既有 archive envelope 先验证，再稳定透传

已有 `bounded_ref` 不是新的 raw candidate。投影先通过 capability 注册并 inspect ref，再比较 placeholder 中存在的 `sha256` 和 `size_bytes` 与 artifact metadata；存在但类型非法或值不一致时返回不含正文的 `integrity_mismatch` envelope。为兼容旧历史，缺失的 sha/size 字段可以从同一次授权 inspect 结果补齐；正常 writer 产生的 placeholder 不发生任何字段变化。continuation hint 仍按既有 `next_offset → offset → 0` 规则补齐。

`archive_page` 必须至少包含合法 artifact ref、sha256、非负 size、page、非负 offset/next_offset/total_units、合法 unit 和 boolean has_more；如果 capability 可用，还要用 ref、sha、size 做授权及元数据一致性校验。`archive_read_error` 必须包含非空字符串 error_type/message；ref 若存在必须是空值或合法 artifact ref，read instruction/preview 若存在必须是字符串。校验失败统一生成 bounded `invalid_archive_read` 或 `integrity_mismatch`，不回显损坏字段；校验通过则保留原始 mapping/JSON-string 表示，禁止递归归档。

**替代方案：**仅按 `kind` 跳过 envelope 会把损坏的 hash、size、offset 或错误字段继续发送给 Provider；每次把合法 page 重新 archive 又会造成 ref 套 ref；均不采用。

### D3.3：声明 archive kind 即进入验证，page 以 artifact 回读为事实来源

archive envelope 的识别与字段合法性分离。投影首先解析结构值：只要值是 mapping/JSON object 且 `kind` 等于 `bounded_ref`，就必须走 bounded-ref 校验，即使 ref 缺失、ref 前缀错误或其他字段损坏；这类值不得被当作 raw candidate。校验失败只生成不携带原始坏字段的 `invalid_archive_read` 或 `integrity_mismatch`，并且不调用 `archive_result`。

page/error 采用显式 allowlist 和有界字段：page 的 `page` 必须是 string/bytes，文本 page 不超过 `ARCHIVE_READ_MAX_LIMIT`，base64 page 不超过该字节上限的编码后最大长度；`read_instructions` 不超过 2048 个 Unicode 字符，error `preview` 不超过 `ARCHIVE_PREVIEW_CHARS`，error `detail` 不超过 200 个字符；offset、next_offset、total_units、size 必须是非负整数，unit/encoding/operation 只能是约定字符串，未知字段不透传。具体上限复用 archive capability 的既有常量，不新建第二套公共结果上限。

当 capability 可用时，page 通过 ref 注册、授权 inspect 和 expected sha/size 校验后，以声明的 offset 和 `next_offset - offset` 作为回读范围；零长度 EOF 使用最小合法读取请求。投影比较 artifact 实际返回的 page、offset、next_offset、total_units、unit、has_more、sha256 和 size_bytes，必要时比较 page_encoding。只有完整匹配的 page 才能原样透传；伪造正文、范围、总量或结束标记统一降级为 bounded `integrity_mismatch`，不把“字段相互自洽”当成真实性证明。无法访问 capability 时仍执行结构/长度校验，但不声称完成内容真实性验证。

error envelope 没有可回读正文，只能做 allowlist、类型、长度和 ref 形状校验；合法错误保持原表示，坏字段不进入错误响应。该规则保持 ArchiveRead 的非递归属性，也避免为了验证 page 而把 page 再写入 archive。

**替代方案：**仅验证 sha/size 或 offset 的单调性无法发现正文和范围被伪造；仅限制顶层结果大小无法阻止 instruction/preview 膨胀；把所有声明字段重新归档会重新制造 ref 套 ref，均不采用。

### D4：采用 Maka 风格 active 最新 step 保护和单次 emergency

request-cycle owner 是 Agent 的一次计划 Provider dispatch；Agent 在调用 context refresh 前创建不可进入 wire 的 cycle state，adapter 只消费该 state，不自行生成第二个 cycle。cycle identity 至少包含 session、当前 Provider request id、canonical source digest/high-water、provider、active_turn_id、system/tools digest 和 budget_bytes。普通 projection、emergency projection 和 final verdict 共享同一 cycle identity。

一个 cycle 的阶段严格为 ordinary → final fit measurement →（仅必要时）emergency → final fit measurement → dispatch 或 capacity verdict。ordinary pass 使用 include_latest_active=false：当前 turn 最新 completed step 保持完整，只裁剪较早 active steps。只要没有新的 completed step，它持续是最新 step，不在 canonical 中写入保护状态；当新 step 完成时，旧 step 自然进入 active 候选。

若且仅当存在明确 budget_bytes，ordinary final measurement 不 fit，且 cycle_state.emergency_used=false，adapter 执行同一 canonical source 的一次 emergency pass，设置 include_latest_active=true，并立即将 emergency_used 标记为 true。该 pass 允许最新 step 在超过 2048 estimated tokens 时归档，已建立的旧 archive ref 必须复用。相同 cycle 的重复 refresh 必须复用已计算 outcome，不得再次 emergency 或发布新 artifact。

emergency 之后仍不 fit 时，final capacity verdict 必须在 SDK dispatch 前返回 ProviderCapacityError 或等价 bounded outcome，并携带 diagnostics、request size/budget 和 cycle identity；不能把已有 placeholder 改写成 capacity_exhausted，不能伪造 tool-level error。没有 budget、没有 active turn metadata 或 ordinary 请求已经 fit 时，不执行 emergency。一个新的 Provider model request 或新的 canonical high-water/active step 会创建新 cycle；overflow retry 不是同一 cycle 的第二次 emergency。

**替代方案：**把 emergency_used 持久化到 canonical event 会污染事实日志；让 projection helper 自己按每次调用生成 cycle 会造成重复 re-entry；无限次重试会导致多轮副作用和 ref 变化；均不采用。

### D5：以 projection outcome 暴露 fail-open diagnostics

归档 projection 使用内部 ArchiveProjectionResult 同时返回 messages、ProjectionDiagnostic sidecar、projection_phase、emergency_used、request_size_bytes 和 request_budget_bytes。ProviderContext.diagnostics 按 canonical replay diagnostics 在前、archive diagnostics 在后的顺序合并，并以 code、event_id、call_id 去重；最多保留 32 条，单条 message 最多 256 个 Unicode 字符，不携带 raw body、artifact 内容或完整 exception text。若 final capacity verdict 不能返回 ProviderContext，ProviderCapacityError 必须保留同一 bounded diagnostics、cycle identity、request size 和 budget，供 Agent/本地 consumer 读取。

对 eligible raw result：

- archive 成功后才可生成 placeholder；placeholder 携带稳定 ref、body hash/size、continuation 和 ArchiveRead 指引；
- archive 失败时必须保留原始 content，diagnostic code 固定为 archive_write_failed 或 archive_identity_unavailable；不返回看似成功的 ref，不返回不可恢复的 archive_read_error 替代 raw；
- raw 仍由完整 Provider request size 和最终 capacity gate 判断，若仍超预算则 fail closed。

已有合法 placeholder 只按自身 ref/body hash/size/continuation 复用，不重新参与 raw candidate 竞争。追加用户、memory、tool suffix、recovery 或聚合 budget 变化都不能改变其 Provider-visible 内容。ArchiveRead page、bounded ref 和 ArchiveRead error envelope 不得被再次归档。

诊断 code 至少覆盖 archive_write_failed、archive_identity_unavailable、replay_metadata_unavailable、synthetic_message_unclassifiable、active_emergency_used 和 provider_capacity_exhausted。诊断按 canonical ordinal、message encounter order 稳定排序；同一 cycle 重复读取不得重复追加相同诊断。archive_write_failed 和 archive_identity_unavailable 属于 fail-open warning，不改变 Provider-visible raw；provider_capacity_exhausted 属于 final verdict，不进入 messages。

**替代方案：**将归档异常转成 Provider-visible error envelope 会丢失原始证据，并且错误 envelope 本身可能无法恢复；仅写日志又无法让本地 consumer 验证失败原因；让 capacity exception 丢弃 diagnostics 又无法定位 final gate，均不采用。

### D6：Provider capacity 和 canonical boundary 保持独立

provider_context.py 继续测量 context-bearing Provider envelope 的 UTF-8 bytes。计入字段集合固定为：Anthropic 的 system、tools、messages 及其 provider-specific content wrapper；OpenAI-compatible 的 system message、tools、messages 及其 provider-specific message/tool wrapper。model、stream、stream_options、HTTP headers 等 transport 字段不计入 context bytes；max_tokens/output reserve 由现有 budget policy 单独保留，不以 /3 估算替代。Agent SDK dispatch gate 仍以 request_fits/ProviderCapacityError 为最终裁决。tool_result.py 继续在 canonical normalization 后按 16 MiB UTF-8 bytes 限制单个公共 tool result。

/3 不参与 Provider capacity 计算、输出 token reserve、费用统计或 SDK retry 预算。归档 projection 只能根据明确的 stale/active/supersession 候选改变表示，不能把 aggregate _fits() 结果当成单条结果的裁剪资格。

最终测量 helper 必须对上述同一字段集合序列化；若后续 Provider adapter 增加会影响上下文的 wrapper，必须同步更新该集合和 contract tests。用 estimated tokens 代替最终 bytes 会低估 system/tools 和 CJK/binary envelope；把所有 transport 参数混入 context budget 又会混淆上下文窗口与请求传输，均不采用。

### D7：测试和验证按调用链分层

测试至少分为四层：

1. estimator/normalization contract：/3 rounding、JSON escaping、CJK/emoji、Base64 和 2048/256 boundaries；
2. projection integration：stale、active、emergency、supersession、archive failure、placeholder idempotency 和 no-ref-on-failure；
3. replay/provider integration：同一 SQLite canonical events 的 cold、incremental、compaction reset、reopen parity，以及 Anthropic/OpenAI wire allowlist；
4. local consumer：真实 Agent refresh 经 CanonicalModelContextAdapter 到两类 fake SDK，验证 ordinary/emergency/final-verdict 阶段、emergency_used、archive publication 次数、transport dispatch 次数和最终零 dispatch。

关键测试必须精确映射到契约：

- estimator fixture 测 canonical serialized text 的 Python Unicode code-point 长度，不测输入字符串长度；
- identity matrix 参数化测试分别覆盖缺失 event id、ordinal、turn、run/invocation、tool call/name、body identity，以及 synthetic compaction message；
- semantic fixture 明确 read_file/list_files/grep_search/run_shell 的工具映射、参数规范化、成功/失败状态和 snapshot allowlist；
- request-cycle fixture 明确同一 cycle 的重复 refresh、ordinary fit、ordinary overflow、single emergency、emergency overflow、new request cycle 和 no-budget/no-active-metadata；
- capacity fixture 明确只计 context-bearing system/tools/messages/wrapper，不把 model、stream、headers 等 transport 字段误算为 context bytes。

验证结果必须区分本地测试、fake SDK/loopback、外部 Provider、部署和 Git 交付证据；本 change 不声称后两者已验证。

**替代方案：**只测试纯 helper 或只比较最终 messages 无法发现 Provider dispatch、artifact 次数、sidecar parity 和 no-dispatch 缺陷，不采用。

## Risks / Trade-offs

- [字符数除以 3 不是 tokenizer 精确值] → 限定为统一 prune heuristic，使用 canonical serialization、ceil 和最终 Provider bytes gate。
- [现有 project_archived_tool_results() 返回 tuple，新增 diagnostics 可能影响调用方] → 通过明确的 outcome contract 更新 Provider 路径和 direct tests；若保留兼容 wrapper，必须由 Provider 路径消费 diagnostics，不能静默丢弃。
- [replay sidecar 与消息过滤/compaction reset 不一致] → cold/incremental 共用 metadata 结构，以 canonical ordinal、artifact ref 和 Provider-visible messages 做 parity assertions。
- [active 最新 step 过大仍可能导致容量失败] → 只允许同一 request cycle 一次 emergency，二次仍不 fit 则 fail closed。
- [Read 范围或 snapshot 误判造成证据丢失] → 仅允许明确参数/allowlist identity；不支持 failure_resolved 和不明关系。
- [归档失败导致重复尝试] → fail-open 保留 raw，记录 bounded diagnostic；稳定 identity 和已发布 ref 负责幂等，失败不得制造成功 placeholder。
- [旧历史消息缺少 metadata] → 使用现有 group/user boundary 兼容回退；缺 identity 时禁止新 semantic 合并，并让最终 capacity gate 保持安全。
- [request-cycle owner 与 Agent refresh/dispatch 边界错位] → cycle state 由 Agent 创建并贯穿 adapter 的 ordinary、emergency、final verdict；同 cycle 重复 refresh 使用 outcome cache，新 Provider request 创建新 cycle。
- [工具映射或路径规范化过宽造成误 supersession] → 只采用显式 read-only descriptor；run_shell 仅允许无副作用的 git snapshot 命令，其余结果不做 snapshot supersession。
- [容量异常时 diagnostics 丢失] → 将 bounded diagnostics 同时放入 ArchiveProjectionResult 和 ProviderCapacityError；测试从 outcome 到 Agent error readback，不只检查 ProviderContext。

## Migration Plan

无需 canonical 数据迁移。旧 bounded ref、旧 artifact metadata 和旧 session snapshot 继续按现有授权/完整性规则读取；新投影只对后续 Provider request 生效。更新旧 archive failure 测试契约时，不改变 canonical event，回滚实现也无需反向改写事件。

发布前先通过 focused、replay/provider integration 和本地 fake SDK consumer 验证，再执行 Python package 全量回归、compileall、OpenSpec strict validation 和 diff check。本 change 不包含 Git、部署或外部 Provider 发布步骤。

## Open Questions

无阻塞性未决问题。/3、2048、256、最近 2 turn 和 active 最新 step 保护已确认；实现时必须采用上述“当前 turn 最新 step 只由 request-cycle emergency 显式纳入”的语义。未来若要暴露 policy 配置、支持 failure_resolved 或扩展更多语义快照工具，应另开 change 并重新评估误判风险。
