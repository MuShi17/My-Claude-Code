# Provider projection P1 实施验证

## 任务与授权

- canonical task source：`openspec/changes/repair-runtime-projection-defects/`
- 本轮授权：用户明确要求“可以开始推进修复”。
- 写入者：主 Agent；本轮没有提交、推送、MR、合并、发布或部署。
- 修改范围：Provider projection、Provider context、Agent SDK dispatch gate、定向测试和本 change 工件；当前新增增量还包括稳定 archive identity、历史 ref 授权兼容、continuation hint 和 replay tool-name fallback。
- 非本轮范围：Maka 仓库、artifact 发布事务、read_file 真正流式读取、ArchiveRead 最终响应 envelope 限制、父子 Agent close barrier 和更广泛历史兼容。

## 实施摘要

1. `archive_projection.py` 将 first-use 候选裁剪与 historical placeholder 复用分离。合法的非 first-use `bounded_ref` 不再依据完整 messages 的 `_fits()` 改写成 `capacity_exhausted`；旧形状只补缺失的 `read_instructions`。
2. `provider_context.py` 增加 Anthropic/OpenAI 的完整 context envelope 计量：Provider messages、Anthropic system、Provider tool definitions 和 Provider-specific tool-result shape 共同进入同一个 `size_fn`。最终结果保留 `request_size_bytes`、`request_budget_bytes` 和 `request_fits`。
3. first-use 的 full/preview/error fallback 都通过完整候选 fit 检查。若最小 fallback 也不适配，抛出 bounded `ProviderCapacityError`，不伪造可发送的 tool-result error。
4. `agent.py` 在 canonical replay 完成后以及两个 SDK helper 进入真实 SDK 调用前都执行 final capacity gate；失败时不更新要发送的 Provider message，也不进入 SDK transport。
5. ArchiveRead page/error 作为已经产生的 tool result 传递，不再被再次归档；容量诊断保留实际 ref 和可调用的 ArchiveRead 指引。

## 本轮审查缺陷及修复结果

独立复核在提交 `6828ece` 的工作树上确认了以下问题；本轮已完成修复：

- 同一 session、runtime event、tool call、tool name 和 body 在 `run-a`/`run-b` 投影时生成不同 logical ref。原因是 `archive_projection.py` 会重新调用 `archive_result()`，而 `archive_capability.py` 将当前 `run_id` 纳入 logical identity；最小复现得到两个 ref 和两个 logical metadata 文件。这会破坏跨 chat run 的 Provider 前缀缓存。修复后 logical identity 排除当前 `run_id`/`parent_run_id`，同一 canonical result 复用 ref、placeholder、底层 blob 和 logical metadata。
- 历史 placeholder 缺少 `read_instructions` 时，`_ensure_read_instructions()` 以默认 offset `0` 生成指引，忽略已有 `next_offset`/`offset`，可能重复读取第一页。修复后按 `next_offset → offset → 0` 选择 offset，并保留合法已有 `limit`。

本轮修复验收结果：稳定 identity 不含当前 run；同 session 双 run 投影得到完全相同的 placeholder/ref 且只保留一个 logical metadata；跨 session 仍隔离；历史 placeholder 指引使用 `next_offset → offset → 0` 并保留有效 limit。注册后的历史 ref 在后续 run 可读，但仍独立执行 session、scope、sha 和 size 校验；未注册 ref 仍执行 lineage 校验。tool message 缺少 `name` 时从 assistant `tool_calls` 按 call id 回退，避免 replay 形状差异生成不同 ref。容量计量、final capacity gate、ArchiveRead 非递归和同 run placeholder 单调性作为已验证基线，不在本轮重复扩展。

## 本地验证

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `python -m pytest src\\rollo\\tests\\test_archive_capability.py src\\rollo\\tests\\test_archive_projection.py -q --disable-warnings --tb=short` | 48 passed | 跨 run identity、历史授权完整性、continuation offset 及原有 ArchiveRead/projection 回归 |
| `python -B -m pytest src\\rollo\\tests\\test_archive_projection.py src\\rollo\\tests\\test_provider_content.py src\\rollo\\tests\\test_local_consumers.py -q --disable-warnings --tb=short` | 67 passed | projection 单调性、fallback、system/tools、Anthropic/OpenAI local consumer 和 no-dispatch |
| `python -m pytest src\\rollo\\tests\\test_archive_projection.py -q --disable-warnings --tb=short` | 32 passed | ArchiveRead exact-fit/impossible-fit、非递归、稳定 placeholder |
| `python -B -m pytest src\\rollo\\tests -q --disable-warnings --tb=short` | 303 passed，存在既有 warning | `src/rollo/tests` 全量 Python 包回归；不等同于根目录 pytest |
| `python -m pytest -q` | benchmark 收集阶段因缺少可选 `harbor` 包失败 | 根目录全仓库验证边界；未将其表述为全仓库通过 |
| `python -m compileall -q src\\rollo` | passed | 编译检查 |
| `openspec validate repair-runtime-projection-defects --type change --strict --no-interactive` | passed | OpenSpec 严格校验 |
| `git diff --check` | passed | 差异空白检查；仅有 Git 的 LF/CRLF 提示 |

## 真实本地消费者证据

`test_local_consumers.py` 使用实际 `Agent` 私有 loop、真实安装的 Anthropic/OpenAI SDK 类和本地 HTTP transport fixture 捕获最终 request。验证覆盖：

- full first-use result 与 capacity-rescue preview；
- 二进制 preview 的 base64 内容和 byte continuation；
- Provider system/tools envelope 的最终字节数与 `request_size_bytes` 一致；
- 超预算时 `_chat_*` 与直接 `_call_*` 均在 transport 前抛出 `ProviderCapacityError`，捕获请求数为 0；
- 后续 ArchiveRead page 保持 page 形状，不产生 ref 套 ref；
- terminal display 不替换 Provider wire content。

这些是本机 fake/local transport 证据，不是外部 Provider、部署或生产网关证据。

## 既有 Provider projection Gap Closure

独立 `test-strategy-agent` 以 `review_mode=delta`、`delta_id=repair-runtime-projection-defects/11.1-11.5` 对当时冻结的工作树完成只读复核，结果为 `sufficient`，G-STR-01～G-STR-04 全部 `closed`，`remaining_gap_ids=[]`，`new_gap_ids=[]`。审查确认当时的 13 个范围文件与冻结内容 digest `c066ceb4de39f9f2acf281696ae1b08b038b237df9270616ef1b76d212707d8a` 一致；该 digest 是 11.x 的历史快照，不代表本轮 12.x 追加代码与测试后的当前工作树。

主 Agent 对审查意见进行独立判断后采纳：A/B/C/D/E/F 均有代码和本地消费者证据，保留“canonical JSON 字节预算不等同于真实 Provider tokenizer/部署限制”这一明确残余风险；未将 artifact identity、真正 chunk read、ArchiveRead 最终 JSON envelope 和父子 Agent close barrier 等排除项误判为本轮缺口。

## 本轮新增 Delta Gap Closure

独立 `test-strategy-agent` 对本轮冻结差异执行 `delta-gap-closure`。初次结果为 `not_closed`，但只指出 G-STR-06/G-STR-07 缺少关键回归断言，并未发现新的可复现产品缺陷。补齐测试后再次复核，结果为 `sufficient`，G-STR-05～G-STR-07 全部 `closed`，`remaining_gap_ids=[]`、`new_gap_ids=[]`：

- G-STR-05（跨 run logical ref 稳定性）：已闭合；双 run projection、单 logical metadata/blob 的回归通过。
- G-STR-06（注册历史 ref 的跨 run 读取与完整性校验）：实现行为已通过临时复现；本轮补充了注册前 lineage 拒绝、注册后真实 `read`、正确 sha/size 成功、错误 sha/size 拒绝的仓库回归。
- G-STR-07（continuation hint）：`next_offset` 优先和合法 `limit` 保留已有回归；本轮补充仅 `offset` 和无 offset/`next_offset` 回退到 0 的参数化回归。

主 Agent 采纳的是“补齐可执行测试证据”这一具体意见，没有无条件接受扩大实现范围的建议；G-STR-01～G-STR-04 不重新打开。

## Replay shape compatibility（P2）

用户提供的复核发现 Model Replay 的 tool message 可能没有 `name`，而归档 identity 仍需要稳定的 `tool_name`。主 Agent 复现确认：同一 event/body 在 tool message 缺失与存在 `name` 的两种形状下，旧实现会生成不同 ref；因此采纳该项兼容性修复。当前实现优先使用 tool message 的非空 `name`，否则使用同一 `tool_call_id` 在 assistant `tool_calls` 中解析出的名称；新增回归断言两种形状的 placeholder/ref 完全一致且只产生一份 logical metadata。

## 残余风险

- 本轮容量 envelope 是保守的 canonical JSON UTF-8 byte 预算，仍不等价于外部 Provider tokenizer 或网关完整限制。
- `10.2/10.3` 的真正 range read 和 ArchiveRead 最终 JSON envelope 动态缩短未在本轮实现。
- `8.1–8.3` 的父子 Agent close/capability 和更广泛历史兼容仍保留为后续 P1。
