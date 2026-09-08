# Provider projection P1 实施验证

## 任务与授权

- canonical task source：`openspec/changes/repair-runtime-projection-defects/`
- 本轮授权：用户明确要求“可以开始推进修复”。
- 写入者：主 Agent；本轮没有提交、推送、MR、合并、发布或部署。
- 修改范围：Provider projection、Provider context、Agent SDK dispatch gate、定向测试和本 change 工件。
- 非本轮范围：Maka 仓库、artifact identity/发布事务、read_file 真正流式读取、ArchiveRead 最终响应 envelope 限制、父子 Agent close barrier 和更广泛历史兼容。

## 实施摘要

1. `archive_projection.py` 将 first-use 候选裁剪与 historical placeholder 复用分离。合法的非 first-use `bounded_ref` 不再依据完整 messages 的 `_fits()` 改写成 `capacity_exhausted`；旧形状只补缺失的 `read_instructions`。
2. `provider_context.py` 增加 Anthropic/OpenAI 的完整 context envelope 计量：Provider messages、Anthropic system、Provider tool definitions 和 Provider-specific tool-result shape 共同进入同一个 `size_fn`。最终结果保留 `request_size_bytes`、`request_budget_bytes` 和 `request_fits`。
3. first-use 的 full/preview/error fallback 都通过完整候选 fit 检查。若最小 fallback 也不适配，抛出 bounded `ProviderCapacityError`，不伪造可发送的 tool-result error。
4. `agent.py` 在 canonical replay 完成后以及两个 SDK helper 进入真实 SDK 调用前都执行 final capacity gate；失败时不更新要发送的 Provider message，也不进入 SDK transport。
5. ArchiveRead page/error 作为已经产生的 tool result 传递，不再被再次归档；容量诊断保留实际 ref 和可调用的 ArchiveRead 指引。

## 本地验证

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `python -m pytest src\\mini_claude\\tests\\test_archive_projection.py src\\mini_claude\\tests\\test_provider_content.py src\\mini_claude\\tests\\test_local_consumers.py -q --disable-warnings --tb=short` | 63 passed | projection 单调性、fallback、system/tools、Anthropic/OpenAI local consumer 和 no-dispatch |
| `python -m pytest src\\mini_claude\\tests\\test_archive_projection.py -q --disable-warnings --tb=short` | 28 passed | ArchiveRead exact-fit/impossible-fit、非递归、稳定 placeholder |
| `python -m pytest src\\mini_claude\\tests -q --disable-warnings --tb=short` | 297 passed，2 个既有 warning | Python 包全量回归 |
| `python -m compileall -q src\\mini_claude` | passed | 编译检查 |
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

## 独立 Gap Closure

独立 `test-strategy-agent` 以 `review_mode=delta`、`delta_id=repair-runtime-projection-defects/11.1-11.5` 对冻结工作树完成只读复核，结果为 `sufficient`，G-STR-01～G-STR-04 全部 `closed`，`remaining_gap_ids=[]`，`new_gap_ids=[]`。审查确认 13 个范围文件与冻结内容 digest `c066ceb4de39f9f2acf281696ae1b08b038b237df9270616ef1b76d212707d8a` 一致；该 digest 对应本次产品实现和 change 工件首次冻结快照，之后仅回写了 `tasks.md` 和本文件中的审查记录，未改变产品代码或测试语义。

主 Agent 对审查意见进行独立判断后采纳：A/B/C/D/E/F 均有代码和本地消费者证据，保留“canonical JSON 字节预算不等同于真实 Provider tokenizer/部署限制”这一明确残余风险；未将 artifact identity、真正 chunk read、ArchiveRead 最终 JSON envelope 和父子 Agent close barrier 等排除项误判为本轮缺口。

## 残余风险

- 本轮容量 envelope 是保守的 canonical JSON UTF-8 byte 预算，仍不等价于外部 Provider tokenizer 或网关完整限制。
- `10.2/10.3` 的真正 range read 和 ArchiveRead 最终 JSON envelope 动态缩短未在本轮实现。
- `8.1–8.3` 的父子 Agent close/capability 和更广泛历史兼容仍保留为后续 P1。
