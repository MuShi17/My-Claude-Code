## Why

当前 Provider projection 主要依据“是否属于最新完整工具组”决定归档，缺少 Maka 所采用的 turn/step 语义和结果规模阈值。这会让同一用户轮次中较早完成的工具结果过早变成 placeholder，也会把较小的历史结果无必要地归档，增加模型回读次数并破坏 Provider 前缀缓存的稳定性。

本 change 将工具结果裁剪拆分为历史 `stale` 和当前轮次 `active` 两套策略，并采用项目明确的 `ceil(序列化结果字符数 / 3)` 估算，使首轮可直接消费的结果保持原样，只有确实值得裁剪的结果才进入归档流程。归档失败必须 fail-open 保留原文，并通过不进入 Provider wire 的有界 diagnostics 暴露；这使归档能力失败与最终 Provider capacity verdict 保持独立。

## What Changes

- 增加按 canonical tool-result 序列化字符数估算的 prune policy：`estimated_tokens = ceil(chars / 3)`。
- 增加历史轮次 `stale` 裁剪：结果超过 2048 estimated tokens 且不属于最近 2 个 turn 时，才替换为稳定的 archive placeholder。
- 增加当前轮次 `active` 裁剪：较早完成的 step 在下一次 Provider 请求前按同一阈值裁剪；最新完成的 step 默认保留一次。
- 为容量紧急路径增加显式例外：只有最终请求仍无法 fit 时，才允许把最新 step 纳入 active 裁剪。
- 对相同结果、覆盖旧范围的 `Read`、同一可证明快照等语义重复提供保守 supersession；结果低于 256 estimated tokens 时不为节省少量上下文而替换。
- 保持 canonical event、canonical tool-result 完整正文、16 MiB UTF-8 字节边界和现有 ArchiveRead 授权协议不变；裁剪只发生在 Provider projection。
- 归档失败时保留原始工具结果，并通过有限数量、有限长度的 projection diagnostics 记录失败；已建立的 placeholder 保持 ref、metadata、continuation hint 和可调用 ArchiveRead 指引稳定。
- 通过 sidecar turn/step 元数据使 cold replay 和 incremental replay 使用相同的裁剪判定；Provider 转换采用 allowlist，内部 metadata 不进入 wire。
- semantic supersession 仅作用于当前 turn 的 active steps，使用 Maka 中可证明的 duplicate、`read_file` 覆盖范围和受支持 snapshot；本 change 不引入 `failure_resolved` 等额外语义。
- 明确单个 Provider request cycle 的 owner、cycle identity、ordinary/emergency/final-verdict 生命周期，以及缺失 replay identity 时各能力的安全降级矩阵。
- 明确 My-Claude-Code 与 Maka 的只读工具语义映射和参数规范化；不对具有副作用的任意 `run_shell` 结果做 snapshot supersession。
- 增加跨 turn、同 turn 多 step、最新 step 保护、容量紧急、语义重复、归档失败、幂等性和双 Provider 本地消费者回归验证。

### 审查修复增量

- 在 canonical final tool-call 边界规范化 JSON object string 参数；旧 canonical event 在 replay 时继续兼容解析。非法 JSON 保留原值，并使 semantic input 标记为 incomplete，不静默转换为 `{}`。
- 对已有 `bounded_ref` 的 sha256/size_bytes 声明与授权 artifact 元数据执行一致性校验；缺失的旧字段可由已验证元数据补齐，错误声明返回有界完整性错误。
- 对 `archive_page` 和 `archive_read_error` 执行最小结构校验；合法 envelope 保持原表示且不重新归档，非法 envelope 降级为有界 `invalid_archive_read` 错误。
- 增加 OpenAI JSON-string 语义淘汰、canonical 参数规范化、placeholder 篡改、page/error 结构损坏和 legacy 字段补齐回归测试。
- 识别 `kind=bounded_ref` 不再依赖 ref 是否已经合法；只要声明了该 kind 就必须进入 envelope 校验，非法 ref 只能降级为有界错误，不得回到普通 raw 归档路径。
- 限制 ArchiveRead page/error 的可选字段类型和长度；page 在授权能力可用时按声明的 offset/range 从 artifact 回读，并核对正文、实际范围、total、unit、has_more 及 sha/size，拒绝“字段彼此自洽但正文伪造”的 envelope。
- 增加非法 bounded_ref、超长 instruction/preview、伪造 page 正文与伪造范围的回归测试。

## Capabilities

### New Capabilities

- `maka-aligned-tool-result-pruning`: 定义 Provider projection 中历史 stale、当前 active、容量紧急和语义 supersession 的裁剪规则，以及 `/3` estimated-token 估算契约。

### Modified Capabilities

<!-- 当前仓库没有主 openspec/specs 目录；既有 archive projection 约束保留在历史 change 中，本 change 通过新能力建立增量契约。 -->

## Impact

- 影响 `src/mini_claude/archive_projection.py`、`src/mini_claude/archive_capability.py`、`src/mini_claude/projections/model_replay_projection.py`、`src/mini_claude/projections/incremental_replay.py`、`src/mini_claude/projections/provider_context.py`、`src/mini_claude/agent.py` 以及必要的 artifact 测试；需要为 archive projection outcome/diagnostics、replay sidecar 和 request-cycle state 定义内部契约。
- 增加或调整 `src/mini_claude/tests/` 中的 Provider projection、archive capability、incremental replay 和本地 SDK consumer 测试。
- 不修改 `D:/workspace/maka`，不改变 canonical SQLite/event 写入，不增加外部依赖，不执行 Provider、部署或 Git 交付。

## Execution Boundary

```yaml
routing_decision:
  profile: high-risk
  issue_gate: skip
  bugfix_path: standard-bugfix
  execution_mode: A
  writer_owner: main-agent
  delivery_authority: local-implementation-only
  required_gates:
    - openspec-contract
    - focused-regression
    - final-local-consumer
    - frozen-diff-review
  forbidden_actions:
    - modify-maka
    - canonical-event-schema-migration
    - commit
    - push
    - deployment

artifact_plan:
  schema_version: 1
  live_task_ledger: required
  conceptual_model: omitted
  openspec: required
  execution_package: omitted
  test_strategy: inline
  independent_review: required
  project_work: omitted
  stable_knowledge: omitted
  additional_artifacts:
    - artifact_type: implementation_validation
      state: required
      reason: Provider projection has separate local consumer and final-capacity acceptance evidence
  reasons:
    openspec: Public Provider-visible behavior and compatibility contract changes
    independent_review: The change needs an adversarial review against Maka without self-acceptance
```
