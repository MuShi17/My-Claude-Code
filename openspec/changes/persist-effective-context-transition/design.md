## Context

See `proposal.md` for the motivation. 当前完整 compaction 已能生成 checkpoint 和 reset event，但写入 checkpoint 与 event 的调用分离；Tier 1-3 仍直接修改 Provider arrays。Model Replay 只看 Canonical events，因此无法知道这些 working-array 修改已经发生。

## Goals / Non-Goals

**Goals:**

- 将所有会影响未来请求的 reduction 变成可验证的 durable transition；
- 让 checkpoint 与 activation event 在 SQLite 中原子提交；
- 让 Replay 只应用已提交的有效上下文变更；
- 保持完整工具消息组和上下文 epoch 的可诊断性。

**Non-Goals:**

- 不在本 change 中实现 run-scoped incremental replay；
- 不改变 Canonical 原始事件或物理删除历史；
- 不重新设计摘要模型；
- 不承诺跨 Provider 的 compaction summary 语义完全相同。

## Decisions

### 1. Transition 作为 Canonical activation fact，checkpoint 作为可验证投影

Checkpoint 保存 bounded summary、recent tail、coverage 和 source digest；activation transition 以 RuntimeEvent 记录其 checkpoint identity、effective context 和 epoch。Replay 先验证 transition 引用的 checkpoint，再应用有效 context。

这样既保留 Canonical Event 的事实来源，又不把 checkpoint 表误当成第二个日志。备选方案是只更新 checkpoint 表，但事件流无法解释某一步开始使用哪一个 checkpoint。

### 2. SQLite 提供组合写入 API

为 SQLite store 增加组合写入路径，在一个 `BEGIN IMMEDIATE` 事务中完成 checkpoint upsert 和 activation event append，并复用普通 event 的 identity、sequence、seal 和 operation 约束。Recording sink 等非 SQLite sink 使用受控的顺序 fallback，并在 activation 失败时不更新 working context。

选择 store-level 事务，是因为在 Agent 层先写 checkpoint 再 emit event 无法保证进程崩溃时两者同时存在。备选方案是应用层补偿，但无法消除数据库提交窗口。

### 3. 轻量 reduction 使用 explicit replacement entries

Tier 1-3 不把完整工具结果再次写入 Canonical Event，而是在 transition payload 中记录目标 event/call identity、replacement value、reason、policy version 和 digest。Replay 将 replacement overlay 应用于对应的完整 call/result group，原始事实仍可审计。

### 4. Context epoch 作为缓存边界

普通追加保持当前 epoch；完整 compaction/reset 生成新 epoch。Provider prefix cache 诊断以 `(session_id, run_id, context_epoch, projection_digest)` 作为有效上下文身份。Epoch 切换不声称旧 prefix 与新 prefix 兼容。

### 5. 失败关闭

checkpoint 或 transition 无法验证时不静默回退到 working array，也不根据当前阈值重新裁剪。保留 Canonical Store，输出无 raw 内容的诊断，并让当前 Run 进入已有 controlled-failure 路径。

## Risks / Trade-offs

- [Risk] transition payload 过大 → 只保存 bounded replacements 和有限 recent tail，大对象继续使用 artifact reference。
- [Risk] 多个轻量 transition 叠加后 overlay 顺序复杂 → 每个 transition 绑定 source high-water/epoch，并在 replay 中按 ordinal 应用。
- [Risk] 非 SQLite sink 无法提供同等事务 → 集成验收将 SQLite 作为权威实现；其它 sink 必须在失败时保持旧 context，不得宣称原子 durable activation。
- [Risk] 旧 checkpoint 缺少 epoch/version → 使用 versioned decoder，无法证明时受控拒绝，不猜测兼容。

## Migration Plan

1. 增加 transition 数据结构、校验和 store 组合写入 API；
2. 先接入完整 compaction activation，验证重启和故障注入；
3. 再将 Tier 1-3 的 in-memory replacement 改为 transition；
4. Replay 应用 transition overlay 并增加工具组完整性检查；
5. 通过后再由增量 Replay change 复用 epoch/high-water cursor。

回滚时停止生成新 transition，保留已提交的 Canonical facts 和 checkpoint；读取端对未知 transition version fail closed，不删除历史或覆盖旧事件。

## Open Questions

无。transition 的 durable 边界、epoch 语义和工具组配对是本 change 的必要契约。

## Post-audit corrections

- Digest-covered model context is finalized before activation. The canonical
  emitter still redacts secret-shaped values, but it must not replace strings
  inside `compaction.context_messages` or transition replacements with a
  different bounded representation after `result_digest` is computed. If a
  secret is redacted, the replacement and transition digest metadata are
  recomputed from the final persisted value after validating the input.
- A replacement is addressed by its exact canonical response event ID. An
  optional tool-call ID is an identity check, not an unscoped fallback, so a
  repeated provider call ID in another run cannot redirect a transition.
