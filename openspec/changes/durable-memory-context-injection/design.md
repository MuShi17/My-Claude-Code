## Context

See `proposal.md` for the motivation. Agent 当前在 Canonical user event 写入之后启动异步 memory prefetch，并在 Provider array 中修改最后一个 user message。该路径既没有 context event，也没有持久化成功后的消费确认。

## Goals / Non-Goals

**Goals:**

- 为模型实际看到的 memory 建立独立 canonical context event；
- 保留原始 user event 的不可变语义；
- 使 Anthropic/OpenAI Replay 使用同一 neutral context representation；
- 支持 commit-aware consumption 和幂等 retry；
- 让 Session/Trace 能区分 user fact 与 orchestration context。

**Non-Goals:**

- 不在本 change 中解决完整 context compression 或 incremental Replay；
- 不把所有 system prompt、skills、MCP catalog 自动持久化为 memory event；
- 不保存 recall provider 的 raw request/response；
- 不允许 memory event 绕过 Canonical redaction/validation。

## Decisions

### 1. 增加 `context` content kind

使用一个 provider-neutral 的 `content.kind = context`，字段包含 `context_type = memory`、`text`、`source`、`content_digest`、`sequence` 和 `idempotency_key`。事件 role 使用 user 以进入模型 history，author 使用 system/host 以区分原始用户输入。

这比伪装成 `text` 更可审计，也比把 memory 写进 actions 更容易由 Model Replay 和 Session Projection 显式消费。

### 2. 先提交，后注入

memory consumer 先构造 context event，并通过 RuntimeEventEmitter 提交；只有 emit 返回成功后，才把相同内容 materialize 到当前请求，且当前请求只追加该 event 的 Replay 结果，不再原地拼接原始 user message。

如果 Canonical sink 失败，不设置 `consumed`，当前 Agent 进入 controlled failure 或显式降级路径，不能 `except Exception: pass`。

### 3. Idempotency key 由召回内容和上下文位置决定

同一 turn、相同 recall source set、相同排序和内容 digest 生成稳定 key。重复消费时先按 key 查找/利用 event identity，避免重试追加重复 event。新的用户 turn 或召回结果变化会产生新 key。

### 4. Projection 保持 Provider-neutral

Model Replay 将 context event 投影成普通 neutral context message，并在 Provider adapter 阶段转为合法 user content。Session/Trace 保留 `context_type`、source 和 digest 元数据；不把原始 recall internals 写入 provider-native blocks。

## Risks / Trade-offs

- [Risk] context event 增加模型历史消息数量 → 将多个同一 recall batch 合并为一个有序 event，保留 bounded text 和 digest。
- [Risk] 延迟 recall 在当前 Provider 请求边界到达 → 只在下一安全请求边界注入，不能修改已经发送的 request 或历史 user event。
- [Risk] memory 文本含秘密 → 走现有 Canonical redaction 和文本 bounding，并在诊断中只保留 source/digest。
- [Risk] 旧 session 没有 context event → 不伪造历史 memory；仅对新注入启用该语义。

## Migration Plan

1. 扩展 RuntimeEvent context kind 校验和 projection contract；
2. 增加 memory event 的 deterministic identity/digest 和 Replay tests；
3. 替换 Anthropic/OpenAI memory consumer，提交成功后才设置 consumed；
4. 增加 fail injection、duplicate retry、restart 和 session/trace tests；
5. 让后续 context transition/incremental replay 复用 context event。

回滚时停止新 context event 注入，不删除已提交事件；读取端对未知/损坏 context event fail closed 或报告诊断，不将其静默当作普通 user text。

## Open Questions

无。memory 的持续上下文语义由本 change 明确采用，transient hint 不属于本 change 的默认路径。
