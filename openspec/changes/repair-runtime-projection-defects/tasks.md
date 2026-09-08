## 1. P0 Contract and Consumer Fixtures

- [x] 1.1 记录 P0 acceptance matrix：final-call identity、ArchiveRead range、Provider budget、terminal 500 字符预算、metrics chronology 和 immutability；将 `partial=true` 排除在 final call 外
- [x] 1.2 固定 Provider 本地预算公式 `effective_window=int(model_context_window*0.70)`、`max(0, effective_window * 4)`、最终 Provider wire message-list fit 检查、preview rescue 上限/递减序列和字符/byte `ArchiveRead` continuation 字段
- [x] 1.3 建立可复用的 P0 测试夹具：等价/冲突/partial call、49,976 字符 artifact、Unicode/bytes page、多 invocation timeline，以及不保存 raw body/secret/path 的 redacted capture
- [x] 1.4 建立最终本地消费者夹具：实际 Anthropic/OpenAI SDK mock transport 接收 Agent 最终 request；CLI 子进程使用本地 protocol stub，不访问外部 Provider，并保存最终 message-list fit 证据

## 2. P0 Canonical Final Tool-Call Integrity

- [x] 2.1 在 runtime event/recorder 侧实现 shared final-call identity/signature：仅接受 `partial=false` + `tool_call_final`，按 `(run_id, call_id)` 幂等去重，等价重复 no-op，冲突返回 bounded `call_identity_conflict`
- [x] 2.2 调整 `agent.py` Provider response path 与 `runtime_lifecycle.py` `DurableToolBoundary`，使 Provider owner 和非 Provider `ensure_final_call` 不产生第二个 final function-call fact
- [x] 2.3 让 durable operation journal 按 `(run_id, call_id)` 读取并复用工具副作用；冲突/不确定状态 fail closed，不自动重试副作用工具
- [x] 2.4 调整 `RuntimeEventReducer`、SessionProjection、ModelReplayProjection、incremental replay、metrics/resume 派生读取，统一使用 shared identity；等价历史重复保留第一条，冲突保留 bounded diagnostic 且不生成可执行 call
- [x] 2.5 增加 partial/final、Provider+boundary 同调用、多工具 distinct IDs、冲突 payload、执行一次、跨 invocation 和历史重复投影回归测试；覆盖 Session/Model/Incremental/Provider fail-closed
- [x] 2.6 增加 fresh-process resume 测试：关闭写入旧重复事件的 store，以新的 Python 进程恢复并构建 Provider context，证明不重复执行、不追加第二个 call entry

## 3. P0 ArchiveRead Range Contract

- [x] 3.1 在 `artifact_archive.py` 明确文本 Unicode character offset、binary byte offset、`total_units`、合法 EOF 和 `offset > total_units` 的校验顺序
- [x] 3.2 在 `archive_capability.py` 固定 `invalid_range`/`limit_exceeded` envelope，禁止越界 slice 变成成功空页，保留 scope/session/hash/size/closed-store 校验
- [x] 3.3 校验成功 page 的 `unit`、`offset`、`next_offset`、`total_units`、`has_more` 与实际返回长度一致，EOF 不产生额外 archive side effect
- [x] 3.4 增加合法 Unicode 分页、binary bytes 分页、空 artifact、精确 EOF、负数/非整数 offset、非正/超大 limit、offset 越界测试
- [x] 3.5 增加 scope mismatch、session mismatch、integrity mismatch、missing ref 和 closed store 的 bounded/path-free fail-closed 测试

## 4. P0 Provider Projection and Final SDK Consumers

- [x] 4.1 冻结并实现首请求 full materialization、按最终 Provider message-list 的 capacity rescue 前缀 preview、stale placeholder、ArchiveRead page 和无 capability 安全降级；不能只按 artifact size 或 neutral list 判断
- [x] 4.2 增加 Anthropic final local SDK consumer：通过实际 Agent request 边界捕获首请求完整结果、capacity rescue、Unicode/bytes preview、stale、ArchiveRead page 和 capacity error，断言 terminal text 未替换 Provider content 且最终 message list fit
- [x] 4.3 增加 OpenAI-compatible final local SDK/loopback consumer：捕获同一组场景的最终 wire messages，断言 system/tool shape、Unicode/bytes preview、最终 message-list fit 和可继续读取
- [x] 4.4 增加 no-capability、inaccessible ref、binary capacity rescue、budget 无法容纳 instruction envelope 的 fail-closed 测试，不发布不可恢复成功 placeholder
- [x] 4.5 增加 cold/warm/reopened store 及真正 fresh-process Provider context 一致性测试，验证 canonical facts/artifact bytes/ref/digest 未被 hydration 修改，且 resume 不重新执行工具

## 5. P0 Terminal Consumer

- [x] 5.1 在 `archive_projection.py`/`ui.py` 对 `bounded_ref`、`archive_page`、`archive_read_error` 增加 typed、正文优先、有界格式化；未知结果保持通用 fallback
- [x] 5.2 保持 terminal 总预算 500 字符，测试长 preview/page 仍有界且尽可能保留正文/错误和 continuation/status，不做全文 hydration
- [x] 5.3 增加 terminal unit/CLI assertions：`read_file` preview、ArchiveRead 成功 page、越界/closed-store error、转义 JSON，以及 path/traceback 不泄漏
- [x] 5.4 启动真实 `mini-claude-py`/`python -m mini_claude` 新进程连接 loopback protocol stub，断言 stdout、session.v2、ArchiveRead continuation 和一次 resume 行为

## 6. P0 Metrics Chronology

- [x] 6.1 修改 `projections/metrics_projection.py`：run `started_at_ms` 只取首个 canonical `invocation_opened`，增加 invocation 级起点/首 token/duration 字段而不覆盖 run 起点；重复 terminal 保留首个边界并产生 bounded error
- [x] 6.2 让 first token 只由显式 `actions["first_token"]` 或 `lifecycle=first_token` 产生；`partial=true` 不得自动设置；缺失时间返回 null
- [x] 6.3 增加单 invocation、多 invocation、partial-only、显式 first token、缺 terminal、异常时间顺序和重复 terminal 测试，断言 first token 非负且 duration 覆盖首个 terminal

## 7. P0 Verification and Acceptance

- [x] 7.1 运行 focused archive/projection/runtime/session-replay/provider/terminal/metrics 测试并记录每个 P0 scenario 的结果
- [x] 7.2 运行 final local SDK consumers、CLI subprocess 和真正 fresh-process resume；保存仅含必要字段/hash/长度的 in-memory redacted evidence，明确不包含外部 Provider/deployment 证据
- [x] 7.3 运行全量 `python -m pytest -q`、`python -m compileall -q src/mini_claude`、`openspec validate repair-runtime-projection-defects --type change --strict --no-interactive` 和 `git diff --check`；根目录测试的 Harbor 收集阻断须诚实记录
- [x] 7.4 主 Agent 对照 `runtime-projection-integrity` 的全部 P0 scenarios 检查实现/测试/证据，复核最终 diff 未修改 Maka、历史 artifact、普通 child allowlist 或无关模块
- [x] 7.5 回写 canonical task source、任务卡和本 change 的实施验证结果；仅在全部 P0 证据闭合后标记完成，仍不授权归档/提交/推送/MR

## Implementation and validation record

本 change 的 P0 实现和验证已完成；8.1–8.3、10.1–10.3 仍是后续 P1，Provider projection 的 11.1–11.5 已在本批次闭合。主要实现落点：

- `src/mini_claude/tool_call_identity.py`、`event_sink.py`、`runtime_lifecycle.py`：统一 final-call identity，按 `(run_id, call_id)` 幂等去重和跨 invocation operation 复用。
- `src/mini_claude/artifact_archive.py`、`archive_capability.py`、`archive_projection.py`：固定 Unicode/bytes range、合法 EOF、越界错误、容量救援和 `capacity_exhausted`，并分离 Provider/terminal projection。
- `src/mini_claude/projections/`、`agent.py`：统一历史 dedup、双 Provider budget/context（有效模型窗口为上下文窗口的 70%）、metrics chronology 和 terminal consumer。
- `src/mini_claude/tests/test_runtime_projection_integrity.py`、`test_local_consumers.py` 及既有回归：覆盖精确 49,976 字符、Unicode/bytes、fresh-process、实际 SDK mock transport、CLI loopback/resume 和 immutability。

验证记录（2026-09-07，当前工作树）：

| 命令 | 结果 | 证据层级 |
| --- | --- | --- |
| `python -m pytest -q src/mini_claude/tests/test_local_consumers.py` | 17 passed | 实际 Agent -> 本地 Anthropic/OpenAI SDK；CLI loopback 新进程 page/error/resume |
| `python -m pytest -q src/mini_claude/tests/test_runtime_projection_integrity.py src/mini_claude/tests/test_archive_projection.py` | 21 passed | P0 focused / fresh-process / projection |
| `python -m pytest -q src/mini_claude/tests` | 249 passed，1 个既有 Windows asyncio proactor transport warning | 本地完整 Python 回归 |
| `python -m pytest -q` | benchmark 测试收集因当前环境缺少 `harbor` 包停止 | 环境边界，不声称全仓库通过 |
| `python -m compileall -q src/mini_claude` | passed | 本地编译检查 |
| `openspec validate repair-runtime-projection-defects --type change --strict --no-interactive` | passed | OpenSpec 严格校验 |
| `git diff --check` | passed | 工作树差异检查 |

真实消费者证据仅指本机实际 SDK 类和 CLI 新进程通过 mock/loopback 协议；未调用外部 Provider、未验证部署。测试 capture 保持进程内，不持久化 raw body、secret 或完整路径。当前工作树未执行 commit、push、MR、merge、release 或 deployment。

## 8. P1 Follow-up（本批次不阻塞）

- [ ] 8.1 用真实父 Agent/子 Agent 新进程场景验证 derived capability 的 scope、普通 child allowlist、汇总 envelope 和 store close barrier
- [ ] 8.2 扩展历史 session 样本和跨版本 projection compatibility，覆盖更多旧 bounded_ref/read_instructions 形状
- [ ] 8.3 评估将 Maka 对齐 capability 与本 change integrity requirements 同步到 `openspec/specs/` 的独立变更

## 9. P0 Artifact identity and canonical serialization follow-up

- [x] 9.1 先补跨 session 同内容、逻辑 ref/共享 blob、旧 hash-only ref 兼容和 metadata identity mismatch 回归测试
- [x] 9.2 实现统一 normalized canonical JSON UTF-8 byte counter：`bytes` 先转 Base64 envelope，公共上限为 16 MiB（16,777,216 字节），覆盖精确边界、+1、CJK/emoji/control/escaping、二进制膨胀和不可序列化值
- [x] 9.3 让 DurableToolBoundary、普通工具/MCP/特殊工具出口和 `archive_result` 统一复用 byte boundary；超限/序列化失败不得写入 artifact 或 canonical success ref
- [x] 9.4 为 logical metadata 增加原子发布与失败回滚；覆盖本地 metadata fault、runtime-store mirror fault、共享 blob 不误删和 recovery diagnostic
- [x] 9.5 让 capability、archive projection、terminal projection 和 ArchiveRead 接受新 logical ref，并验证 ArchiveRead/ArchiveRead page 不会产生 ref 套 ref
- [x] 9.6 运行 P0 focused tests、fresh-process/local consumer 回归、全量 Python tests、compileall、OpenSpec strict validate 和 `git diff --check`；回写本任务段的证据与残余 P1 边界

验证记录（2026-09-08）：

- P0 focused tests：`python -m pytest src\\mini_claude\\tests\\test_tool_result_boundary.py src\\mini_claude\\tests\\test_archive_capability.py src\\mini_claude\\tests\\test_archive_projection.py src\\mini_claude\\tests\\test_compaction_artifacts.py -q --disable-warnings --tb=short`，64 passed。
- 本轮 Provider projection focused tests：`python -m pytest src\\mini_claude\\tests\\test_archive_projection.py src\\mini_claude\\tests\\test_provider_content.py src\\mini_claude\\tests\\test_local_consumers.py -q --disable-warnings --tb=short`，63 passed。
- fresh-process/local consumer 回归及全量 Python tests：`python -m pytest src\\mini_claude\\tests -q --disable-warnings --tb=short`，297 passed，2 个既有 warning。
- `python -m compileall -q src\\mini_claude` 通过；本 change 通过 `openspec validate repair-runtime-projection-defects --type change --strict --no-interactive`；`git diff --check` 通过（仅有 Git 的换行符提示）。
- 根目录 `python -m pytest -q` 未作为通过证据：benchmark 测试收集阶段缺少可选依赖 `harbor`（`ModuleNotFoundError`），与本次 Python 包实现无关。
- 仍保留的 P1 边界：8.1–8.3、10.1–10.3；包括父子 Agent close barrier、更多历史版本兼容、真正的增量/range 读取，以及 ArchiveRead 最终 JSON envelope 的完整响应字节预算。本轮 11.x 的 Provider projection 单调性和最终容量 gate 已实现，独立 Gap Closure 证据见 `implementation-validation.md`。

## 10. Deferred P1 scope

- [ ] 10.1 将完整 Provider request envelope（system/tools/output reserve）纳入独立容量校准，而不是只统计 message list
- [ ] 10.2 评估 workspace `read_file` 的真正增量读取和 ArchiveRead 的 range read，避免分页时重复加载/校验完整 payload
- [ ] 10.3 对 ArchiveRead 最终 JSON envelope 做完整响应字节上限控制，必要时按 page 内容动态缩短

## 11. P1 Provider projection monotonicity and final capacity gate

- [x] 11.1 对非 first-use 的合法 `bounded_ref` 移除 aggregate `_fits()` 决策，保持 ref、placeholder metadata 和 `ArchiveRead` 指引在 suffix 增长后的 Provider projection 中稳定；兼容缺少 `read_instructions` 的历史形状
- [x] 11.2 让 Provider context adapter 接受当前 Provider 的 system/tools 形状，计算完整 context envelope 的字节数，并保留 `effective_window=int(model_context_window*0.70)`、`budget_bytes=max(0,effective_window*4)` 约定
- [x] 11.3 让 first-use tool-level fallback 先通过完整候选 `size_fn`；fallback 自身不 fit 时返回 bounded `ProviderCapacityError`，projection 完成后再由 final capacity gate 阻止 aggregate 超限 SDK dispatch，不把已有 placeholder 改成 `capacity_exhausted`，不回写 canonical
- [x] 11.4 补齐 G-STR-01～G-STR-04：suffix 临界预算单调性、fallback/error exact-fit 与 impossible-fit、system/messages/tools 完整预算、first-use prune 后 final verdict 顺序；覆盖 Anthropic/OpenAI adapter
- [x] 11.5 通过实际 Agent -> 本地 fake SDK/loopback consumer 验证完整 context envelope 与 no-dispatch，并回写 implementation validation、Gap Closure 和残余风险；不执行 Git 交付
