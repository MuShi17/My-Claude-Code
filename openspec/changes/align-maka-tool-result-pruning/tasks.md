## 1. 建立统一估算、sidecar 和 identity matrix

- [x] 1.1 复用 tool_result.py 的 canonical_tool_result_bytes() 建立唯一 prune estimator，按规范化 JSON serialized text 的 Python Unicode code-point 数执行 ceil(chars / 3)，并集中定义 2048、256、最近 2 turn 和 active 策略常量。
- [x] 1.2 增加不可变 ReplayMessageMeta sidecar，明确 runtime event、canonical ordinal、turn/run/invocation、step、tool call/name、arguments digest、range identity、body identity 和 terminal status 字段来源。
- [x] 1.3 定义 identity_state=complete/partial/synthetic 的 capability matrix：分别规定缺失 event id、ordinal、turn、run/invocation、tool call/name、body identity 时 stale、active、archive reuse、semantic supersession 的禁用范围和 diagnostic code。
- [x] 1.4 在 ModelReplayResult、IncrementalModelReplayCursor、compaction reset、reopen 和 recovery rebuild 中同步创建、过滤和重建 sidecar；synthetic context message 不得复用同一个 reset event id 作为真实工具身份。
- [x] 1.5 为 Anthropic/OpenAI 转换增加显式 Provider field allowlist，递归过滤 sidecar，确保 sidecar 不进入 neutral digest 或 wire，同时保留 Provider 必需的合法 tool-call 标识。

## 2. 实现 Maka 对齐的 stale/active 分类

- [x] 2.1 按 canonical ordinal 的 turn 首次出现顺序计算最近 2 个受保护 turn，不按 turn_id 字符串排序；实现旧大结果、旧小结果、近期大结果和缺 ordinal 回退。
- [x] 2.2 将同一 run_id/invocation_id 的完成 tool-call group 建模为 active step；普通 active pass 只归档当前 turn 较早且超过 2048 estimated tokens 的 step。
- [x] 2.3 明确最新 completed step 的 request-cycle 保护语义：普通 pass 排除最新 step；只有同 cycle emergency pass 可以纳入；不向 canonical event 写入保护状态。
- [x] 2.4 建立 My-Claude-Code 到 Maka descriptor 的显式映射：read_file→Read、list_files→Glob、grep_search→Grep、run_shell→Bash；实现参数规范化、默认 offset/limit、路径规范化和 snapshot key。
- [x] 2.5 将 run_shell snapshot 限定为前台、无 shell 元字符/命令替换的只读 git status/diff/log/show/branch/rev-parse 命令；其他 shell 结果只允许 exact duplicate，不做 snapshot supersession。
- [x] 2.6 将 semantic supersession 限定为当前 turn active steps，按 tool name、canonical input、success/error 状态、body SHA-256 计算 exact key；实现 read range coverage 和 supported snapshot，不实现 failure_resolved。
- [x] 2.7 对并行调用、失败覆盖成功、未知工具、缺失参数和无法证明 identity 的场景保持原文并记录稳定 bounded diagnostic。

## 3. 实现 archive outcome、request-cycle 和容量流程

- [x] 3.1 定义不可变 ArchiveProjectionResult，至少包含 messages、projection phase、emergency_used、request size/budget 和 diagnostics；ProviderContext 合并 canonical 与 archive diagnostics，按 code/event/call 去重、ordinal/encounter 排序，最多 32 条且单条最多 256 Unicode 字符。
- [x] 3.2 固定 archive_write_failed、archive_identity_unavailable、replay_metadata_unavailable、synthetic_message_unclassifiable、active_emergency_used、provider_capacity_exhausted diagnostic code；禁止 raw body、artifact content 和完整 exception text。
- [x] 3.3 修改归档失败路径为 fail-open：保留原始 content，不生成成功 ref 或 archive_read_error 替代物；若 final capacity 仍不足，ProviderCapacityError/等价 outcome 必须携带 bounded diagnostics、cycle identity、request size/budget。
- [x] 3.4 由 Agent 创建并持有 request-cycle state，定义 cycle identity、ordinary→fit→emergency→fit→dispatch/final verdict 阶段、emergency_used 和同 cycle 重复 refresh 缓存；新的 Provider request/high-water/active step 创建新 cycle。
- [x] 3.5 接入 ordinary pass → context-bearing Provider byte fit → 最多一次 emergency pass → final capacity verdict 流程；无 budget 或无 active metadata 时不执行 emergency，最终超限时 SDK 调用数必须为 0。
- [x] 3.6 对合法 placeholder、ArchiveRead page、bounded ref 和错误 envelope 实施结构/授权校验后透传，禁止 ref 套 ref、重复归档和 suffix/capacity 导致的内容变形。
- [x] 3.7 保持 archive logical identity 基于 canonical event/tool-call/body identity，跨 chat run、suffix、recovery 和重复 projection 复用同一 ref、hash、size、continuation 和 metadata。

## 4. 补齐 projection、semantic 和 replay 回归测试

- [x] 4.1 增加 estimator contract 测试：canonical JSON structure/escaping、CJK/emoji、bytes-to-Base64、恰好 6144/6145 字符及 2048/2049 token 边界；fixture 必须基于 serialized text 长度。
- [x] 4.2 增加 stale 测试：至少 3 个 turn、按 canonical ordinal 保护最近 2 个 turn、旧大结果归档、旧小结果 inline、近期大结果不因 aggregate budget 归档。
- [x] 4.3 增加 active 测试：同 turn 多 step、单 step protection、追加新 step 后旧 step 可裁剪、重复投影幂等和 artifact publication count 不增长。
- [x] 4.4 增加工具语义测试：四种 My 工具映射、read_file default/range coverage、path normalization、Glob/Grep snapshot、run_shell git snapshot allowlist 和非 allowlist 保留。
- [x] 4.5 增加 semantic supersession 测试：duplicate、read_file 覆盖、snapshot，以及小结果、并行、failure/success、unknown tool 和缺失 identity 保持原文。
- [x] 4.6 更新既有 archive failure 回归：注入 artifact.write/archive.write 失败时断言 raw 保留、无 artifact metadata/成功 ref、diagnostic code/上限正确且不进入 Provider message。
- [x] 4.7 增加 ArchiveRead page/bounded-ref/error envelope 非递归测试，验证 ref、hash、size、continuation 和 instruction 稳定。
- [x] 4.8 增加 sidecar identity matrix 测试：complete/partial/synthetic 以及缺失 event/ordinal/turn/run/invocation/tool/name/body identity 的逐字段降级。
- [x] 4.9 增加 cold/incremental parity 测试：同一 SQLite canonical event 集合覆盖至少 3 个历史 turn、当前多 step、compaction reset、reopen、recovery；比较 messages、sidecar、identity_state、diagnostics、refs、顺序和 artifact count。

## 5. 真实本地消费者与 Provider capacity 验证

- [x] 5.1 使用真实 Agent refresh、CanonicalModelContextAdapter、临时 ArtifactArchive 和 fake Anthropic SDK 验证 ordinary fit 时 emergency=0、overflow 时 emergency<=1、成功 dispatch 次数和 archive publication 次数。
- [x] 5.2 使用同一真实本地链路验证 OpenAI-compatible SDK 的 emergency、messages/tools wire allowlist、metadata non-leakage 和最终容量拒发。
- [x] 5.3 覆盖同 request-cycle 重复 refresh、new Provider request、new high-water、new active step、budget=None 和 no-active-metadata，断言 cycle/emergency 状态和 artifact count。
- [x] 5.4 验证 archive failure 从 ArchiveProjectionResult 到 ProviderContext/ProviderCapacityError diagnostics 的完整 readback，并断言两类 fake SDK 均不收到诊断内部字段或 raw replacement error。
- [x] 5.5 验证 context-bearing envelope 的固定计量字段：Anthropic system/tools/messages/wrappers、OpenAI system message/tools/messages/wrappers；明确 model/stream/headers 不计入 context bytes，max_tokens/output reserve 使用既有 policy。
- [x] 5.6 保留并回归公共 canonical result 16 MiB exact/+1 边界，验证 normalized bytes、canonical event 不变和超限前不 dispatch。
- [x] 5.7 验证 canonical SQLite/event bytes 在所有 projection、archive failure、recovery 和 capacity failure 路径中保持不变。

## 6. 验证记录与交付边界

- [x] 6.1 运行 archive/projection/provider/replay/local-consumer focused tests，记录每条新增 requirement/scenario 对应的命令、结果和诊断证据。
- [x] 6.2 运行 src/mini_claude/tests 全量测试、compileall、OpenSpec strict validation 和 git diff --check，区分可选 benchmark 依赖残留。
- [x] 6.3 生成 implementation-validation.md，区分本地测试、fake SDK/loopback、外部 Provider、部署和 Git 交付证据，记录未验证边界。
- [x] 6.4 由主 Agent 对照 proposal/design/spec/tasks 复核最终 diff、request-cycle owner、identity matrix 和 deferred risks；commit、push、MR、部署等交付动作须另获明确授权。

## 7. 修复审查发现的参数与 archive envelope 缺陷

- [x] 7.1 在 `ModelCallRecorder.final_tool_call()` 与 replay metadata 边界复用 `decode_tool_arguments()`；合法 JSON object string 规范化为 mapping，非法值保留原值并让 semantic input 保持 incomplete。
- [x] 7.2 在既有 `bounded_ref` projection 中比较 placeholder sha256/size_bytes 与授权 artifact metadata；缺失旧字段从 metadata 补齐，错误声明返回 bounded integrity mismatch，且不改变合法 placeholder。
- [x] 7.3 为 `archive_page` 与 `archive_read_error` 增加 bounded schema 校验、ref/hash/size/range 一致性校验和 malformed envelope 安全降级，保留合法 envelope 的非递归透传。
- [x] 7.4 增加 canonical recorder、OpenAI JSON-string semantic supersession、非法参数、placeholder 篡改/legacy 补齐和 page/error 损坏回归测试，并补真实 OpenAI local consumer 的语义断言。
- [x] 7.5 运行新增聚焦测试、原有 package 全量测试、compileall、OpenSpec strict validation 和 diff check，回写本 change 的 implementation-validation.md。

## 8. 修复 archive envelope 的识别、边界与 page provenance 缺陷

- [x] 8.1 将 `kind=bounded_ref` 的声明识别与 ref 合法性解耦；非法 bounded_ref 进入 bounded 校验并返回错误，禁止普通 `archive_result`。
- [x] 8.2 为 ArchiveRead page/error 建立字段 allowlist、类型/长度上限和未知字段拒绝规则，确保 instruction、preview、detail 和 page 正文有界。
- [x] 8.3 对有 capability 的 page 按声明 offset/range 回读授权 artifact，比较实际 page、范围、total、unit、has_more、sha/size 和必要 encoding；伪造内容统一 integrity mismatch 且不回显坏字段。
- [x] 8.4 增加非法 bounded_ref、artifact 数量不增长、超长可选字段、伪造 page 正文/范围和合法 page 透传回归测试。
- [x] 8.5 运行新增聚焦测试、包内全量测试、compileall、OpenSpec strict validation 和 diff check，并更新 implementation-validation.md 与任务状态。
