# Maka 对齐的工具结果裁剪实施验证

## 任务与授权

- canonical task source：`openspec/changes/align-maka-tool-result-pruning/`
- 本轮授权：用户明确要求使用 `openspec-apply-change` 继续按已批准草案实施。
- 写入者：主 Agent；本轮未执行 commit、push、MR、merge、release 或 deployment。
- 对照范围：只读参考 `D:/workspace/maka` 的 stale/active tool-result prune、descriptor 和 archive placeholder 行为；未修改 Maka。
- 非本轮范围：canonical SQLite/Event schema migration、Maka 仓库修改、外部 Provider/生产网关验证、commit/push/deployment。

## 实施摘要

1. `tool_result.py` 复用 canonical tool-result serialization，先规范化 JSON/bytes，再按 serialized text 的 Python Unicode code-point 数计算 `ceil(chars / 3)`；2048 ordinary prune、256 semantic supersession、最近 2 个 turn 等策略常量集中定义，公共结果上限继续按规范化 UTF-8 bytes 的 16 MiB 执行。
2. 新增不可变 `ReplayMessageMeta` sidecar，与 neutral replay message 一一对齐，携带 canonical event/ordinal、turn/run/invocation、step、tool call/name、arguments/body identity、read range、snapshot、完成状态和 `identity_state`。sidecar 不参与 neutral digest，也不进入 Provider wire。
3. `archive_projection.py` 改为 Maka 风格的 stale/active 两条候选路径：旧于最近 2 个 turn 且超过 2048 estimated tokens 的结果才走 stale；当前 turn 中较早完成的 step 才走 ordinary active；最新 step 只有同一 request cycle 的 emergency pass 才能被纳入。已有 placeholder、ArchiveRead page/error 只校验并透传，不重新归档。
4. semantic supersession 仅作用于明确的 `active_turn_id`，并且只支持可证明的 duplicate、成功 `read_file` range coverage、Glob/Grep/Bash snapshot；并行调用、失败覆盖成功、未知工具、缺失参数和缺失 identity 均 fail-open 保留原文。该 active-turn 过滤是最终复核中补上的边界修复。
5. Provider projection 返回 `ArchiveProjectionResult`，Agent 持有一次 Provider request cycle；ordinary pass 后测量包含 Provider system/tools/messages/wrappers 的最终 envelope，最多执行一次 emergency，再由 final capacity verdict 决定是否允许 SDK dispatch。无 budget、无 active sidecar 或仍超限时不执行/不重复 emergency。
6. 归档失败只记录 bounded diagnostic 并保留 raw content；不生成伪造 ref 或不可恢复的 tool-level error。旧的未调用 `_fits`/capacity rescue 路径已删除，避免回到按整段 messages 判断单条结果的旧语义。
7. 稳定 logical archive identity 沿用 canonical event、tool call、tool name、body 和 session identity，不包含当前 chat run；同一 canonical result 跨 run、suffix、reopen 和重复 projection 复用 ref/metadata，底层相同 blob 可复用而 session 授权仍隔离。
8. archive envelope 的声明识别与内容验证已进一步收紧：声明 `kind=bounded_ref` 的值即使 ref 非法也不会回到 raw candidate；page/error 使用 allowlist 和字段上限，有 capability 时按声明范围回读 artifact 并比较正文、范围、总量、unit、has_more 及完整性字段。

## 修改文件

- `src/rollo/tool_result.py`
- `src/rollo/projections/replay_metadata.py`
- `src/rollo/projections/model_replay_projection.py`
- `src/rollo/projections/incremental_replay.py`
- `src/rollo/projections/provider_context.py`
- `src/rollo/projections/__init__.py`
- `src/rollo/archive_projection.py`
- `src/rollo/provider_capacity.py`
- `src/rollo/agent.py`
- `src/rollo/runtime_lifecycle.py`
- `src/rollo/tests/test_maka_pruning_contract.py`
- `src/rollo/tests/test_archive_projection.py`
- `src/rollo/tests/test_projections.py`
- `src/rollo/tests/test_provider_content.py`
- `src/rollo/tests/test_incremental_replay.py`
- `src/rollo/tests/test_local_consumers.py`
- `src/rollo/tests/test_runtime_lifecycle.py`
- `openspec/changes/align-maka-tool-result-pruning/tasks.md`
- `openspec/changes/align-maka-tool-result-pruning/implementation-validation.md`

## 审查修复增量

针对审查发现的 OpenAI 参数形状和 ArchiveRead envelope 缺陷，本轮补充了以下实现：

- `ModelCallRecorder.final_tool_call()` 在 canonical final-call 边界解码合法 JSON object string；非法 JSON 或非 object 保留原值，不静默改写为 `{}`。
- replay metadata 对旧 canonical JSON string 使用同一 decoder，使 OpenAI 的 `read_file` range、重复结果和 snapshot 语义与 mapping 参数一致；非法参数保持 incomplete、fail-open。
- 已有 `bounded_ref` 在 session 授权后校验 artifact 的 sha256/size_bytes；缺失的旧字段从同一 metadata 补齐，篡改或类型错误只产生 bounded `integrity_mismatch`。
- `archive_page` 和 `archive_read_error` 在重放时执行 bounded 结构校验；合法 envelope 原样透传，非法字段降级为不携带原始坏字段的 `invalid_archive_read`，不进入再次归档。

针对第二轮 P2 审查，本轮继续补充：

- 以 `kind=bounded_ref` 的声明识别替代“合法 ref 才算既有归档”的判断；非法 ref 直接生成 bounded `invalid_archive_read`，并用带完整 sidecar 的 active fixture 验证 artifact 数量不增长。
- 为 page/error 增加显式字段 allowlist、类型和长度边界；page 正文限制到 ArchiveRead 单页上限，二进制 Base64 按解码后的 byte range 限制，`read_instructions`、`preview`、`detail` 分别受 2048/4000/200 字符上限约束。
- 有 capability 时对合法 page 使用 `next_offset - offset` 回读授权 artifact（EOF 使用最小合法 limit），比较实际 page、offset/next_offset/total_units、unit、has_more、sha/size 和可见元数据；正文或范围伪造只返回不带坏字段的 `integrity_mismatch`。
- 原有不完整伪造 ref 的 provider-content 夹具同步到新契约：声明 envelope 的语法校验优先于 capability unavailable 降级，仍验证 JSON wire 的稳定性和无正文泄漏。

本轮新增回归覆盖 canonical recorder、OpenAI JSON-string semantic supersession、非法参数、placeholder integrity 篡改/legacy 补齐、ArchiveRead page/error 损坏，以及真实 OpenAI 本地 loopback consumer 的三请求语义淘汰路径。

## 验证记录

以下命令均在 `D:\workspace\My-Claude-Code`、PowerShell、UTF-8 环境执行。

| 命令 | 结果 | 证明范围 |
| --- | --- | --- |
| `python -m pytest -q src/rollo/tests/test_archive_projection.py src/rollo/tests/test_local_consumers.py src/rollo/tests/test_maka_pruning_contract.py --tb=short` | 82 passed | ArchiveRead 非递归、placeholder 单调性、Maka stale/active/semantic、双 Provider 本地 consumer、capacity gate、OpenAI JSON-string 真实 loopback consumer，以及本轮 invalid bounded_ref/page provenance 回归 |
| `python -m pytest -q src/rollo/tests --tb=short` | 358 passed，1 warning | `src/rollo/tests` Python 包全量回归；1 个 Windows Proactor unraisable transport warning，未形成测试失败 |
| `python -m compileall -q src/rollo` | passed | Python 编译检查 |
| `openspec validate align-maka-tool-result-pruning --strict` | passed | OpenSpec change 工件严格校验 |
| `git diff --check` | passed | 差异空白检查；Git 仅提示部分工作树文件的 LF/CRLF 转换 |

此外，`test_maka_pruning_contract.py` 当前收集到 19 个契约测试，包含 canonical serialized text 的 6144/6145 code-point 与 2048/2049 estimated-token 边界、最近 2 turn、active emergency/cycle、current-turn semantic filter、identity matrix、archive failure、跨 run 单 logical metadata、cold/incremental parity、SQLite reopen 和无 budget/no active sidecar。`test_incremental_replay.py` 的 compaction reset 和 sidecar parity 断言也包含在包内全量结果中。

## 本地消费者证据

`test_local_consumers.py` 使用本仓库实际 `Agent`、`CanonicalModelContextAdapter`、临时 `ArtifactArchive`、已安装的 Anthropic/OpenAI SDK 类和本地 `httpx` transport fixture 捕获最终请求，验证了：

- 首次 ordinary fit 保留完整工具结果；容量不足时最多一次 active emergency，并生成可调用的稳定 ArchiveRead placeholder；
- Anthropic/OpenAI 的 system、tools、messages 和 provider-specific wrapper 计量与最终捕获请求一致；sidecar、运行时 id 和测试元数据不进入 wire；
- ArchiveRead page、binary byte continuation 和已有 bounded ref 不被再次归档，不发生 ref 套 ref；
- archive failure 保留 raw，capacity failure 携带 bounded diagnostics/cycle identity，超预算时两类 SDK 的 dispatch 次数均为 0；
- 最终 SDK helper 仍执行独立 capacity gate，不能通过在 projection 后追加 suffix 绕过预算。

这些是本机 fake/local transport 证据，不是外部 Provider、部署、网关或生产磁盘 I/O 验收。

## 主 Agent 最终复核

- `proposal.md`、`design.md`、新增 capability spec 与 `tasks.md` 已按当前实现同步；1.x–5.x 任务已由对应代码和测试证据闭合，6.x 在本验证记录和最终 diff 复核后闭合。
- 7.1–7.4 的审查修复已由实现和回归证据闭合；7.5 的聚焦/全量测试、compileall、OpenSpec strict validation 和 diff check 均通过。
- 8.1–8.5 已由 invalid bounded_ref 不递归归档、page/error bounded envelope、artifact 回读比对、新增回归测试及最终验证命令闭合。
- canonical event、canonical SQLite bytes 和公共 16 MiB boundary 未被 projection 改写；sidecar 是内存投影辅助信息，reopen/replay 由 canonical source 重建。
- request-cycle owner 是 Agent 的单次 Provider model request；cycle identity 变化于 Provider request id、canonical source high-water/digest、provider、active turn、tools/system 或 budget 变化；同一 cycle 的 refresh 复用 outcome 并禁止第二次 emergency。
- 缺失 replay identity 时采用 conservative fail-open：不创建新 archive ref、不做 active/semantic 猜测，并只产生固定 bounded diagnostic。
- 没有无条件采纳“把所有 Maka 细节都复制进来”的建议：本 change 保留项目确认的 `/3` estimator、16 MiB canonical boundary、现有 Provider budget policy，并未引入 Maka 的 `failure_resolved` 或副作用 shell snapshot。

## 未验证边界与残余风险

- `/3` 只是裁剪启发式，不等同于 Provider tokenizer；Provider capacity 仍使用完整 context-bearing envelope 的 canonical UTF-8 bytes 和既有 output reserve policy。
- 本轮未执行真实外部 Anthropic/OpenAI 请求、部署、生产网关或跨进程多 writer 验证；fake SDK 只证明本地 Agent 到请求构造/dispatch gate 的链路。
- 根目录全仓库 pytest 未作为通过依据；本轮验收范围是 `src/rollo/tests`，benchmark/Harbor 等可选外部依赖未纳入。
- `run_shell` snapshot 仍只接受无 shell 元字符的前台只读 git 查询；任意副作用 shell 不做 snapshot supersession，`failure_resolved` 语义另行处理。
- ArchiveRead 最终 JSON envelope 的动态 page 缩短和真正文件 range streaming 不在本 change 内；本轮只确保现有 page/continuation 在 projection 中不递归、不变形。
