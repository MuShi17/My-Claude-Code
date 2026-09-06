## Context

See `proposal.md` for the motivation. 当前大工具结果已经经过 redaction、bounding 和 artifact archive，但 Provider-visible value 仍在 Agent、Replay 和 Provider adapter 之间重复编码。不同路径的字典顺序和 separators 不一致，导致同一 Canonical 事实可能产生不同的请求字节。

## Goals / Non-Goals

**Goals:**

- 建立一个可被首次请求、Replay 和压缩共同调用的确定性 tool-result materializer；
- 将 neutral model history 与 Anthropic/OpenAI wire shape 明确分层；
- 保证 SQLite reload 后的 provider-visible tool result 与首次 materialization 字节一致；
- 对任意 list 和合法 content blocks 做区分，避免 Provider 反序列化错误；
- 保留现有 redaction、artifact reference 和 controlled-failure 边界。

**Non-Goals:**

- 不改变 RuntimeEvent v2 的事实模型或 Canonical authority；
- 不在本 change 中实现压缩 transition、memory durable event 或增量 Replay；
- 不引入新的 Provider SDK 或依赖；
- 不承诺不同 Provider 之间的 wire payload 相同，只要求各自合法且同一 Provider 内可重建。

## Decisions

### 1. 以 Provider-visible canonical value 作为单一中间边界

工具结果完成 redaction、bounding、artifact archive 后，先转换成一个明确的中间表示。文本保持字符串；普通 mapping/sequence 转换为确定性 JSON 文本；只有满足完整 block schema 的序列才允许走 content-block 分支。

选择这一边界，是为了避免 Agent 首次请求和 Replay 各自猜测如何编码。备选方案是只在 Provider adapter 中修补 dict，但那不能保证 Agent 首次请求、checkpoint 和 Replay 使用同一表示。

### 2. 统一使用稳定 JSON 编码

结构化 JSON 使用与 Canonical Event 相同的核心规则：UTF-8、非 ASCII 不转义、递归 key 排序、固定 separators、拒绝 NaN/Infinity。该编码器只负责已经完成 redaction/bounding 的安全值，不负责隐式序列化任意 SDK 对象。

选择复用 Canonical 规则，是为了让 digest、事件 payload 和 Provider-visible value 具有可解释的关系。备选方案是保留 Python insertion order，但它会依赖对象来源和 JSON reload 顺序，不能作为缓存稳定性契约。

### 3. Provider adapter 负责最终 wire shape

Replay 先生成 neutral model history，再由 Anthropic/OpenAI adapter 生成各自合法的消息。Anthropic `tool_result.content` 只接受字符串或经过验证的 block list；OpenAI-compatible tool message 使用其允许的字符串/JSON 形状。

选择 adapter 分层，是为了避免把 Anthropic 私有的 `thinking`、`signature` 或 `tool_result` block 直接写进通用 RuntimeEvent。备选方案是把 Provider wire payload 原样存入 Canonical，但这会把 Provider 兼容性细节提升为事实源，并增加跨 Provider 恢复成本。

### 4. 用请求捕获验证 bytes，而不只验证对象相等

测试 fake provider 接收最终发送的 request payload，并比较首次请求、下一轮 Replay、SQLite reload 后 Replay 的 serialized bytes。对象 deep-equal 只证明语义近似相同，不能证明前缀缓存使用的 wire bytes 相同。

### 5. 失败时沿用现有 controlled-failure

无法安全归一化的值不通过 `str(value)` 静默转换，也不把异常 object 直接写入事件。先生成无敏感信息的类型诊断，再让当前 Run 进入已有失败路径。这样既避免 Provider 400，也避免为了继续运行破坏 Canonical contract。

## Risks / Trade-offs

- [Risk] 确定性 JSON 可能改变已有结构化结果的空格或字段顺序 → 将变更限制在结构化 tool result，并用请求级回归测试固定新表示。
- [Risk] 某些 Provider 支持的 content block 类型比当前 neutral schema 更丰富 → 只允许已声明且有测试覆盖的 block 类型，未知类型走 JSON 或 controlled failure。
- [Risk] 首次请求仍可能绕过新 materializer → 在 Agent、Replay、压缩输入三个入口分别加入集成断言，并禁止调用方自行 `json.dumps`。
- [Risk] Provider SDK 在内部再次序列化或添加字段 → 测试捕获传给 SDK 的 payload；Provider 实际 cache 命中率另由 usage/telemetry 验证。
- [Risk] 旧 fixture 依赖 insertion-order JSON → 更新 fixture 为显式 canonical bytes，并保留兼容性说明，不通过放宽校验来维持不稳定表示。

## Migration Plan

1. 先加入 materializer 和 provider adapter 单元测试，不改变 Canonical Event schema；
2. 接入 Agent 首次 tool-result 请求和 Replay 路径；
3. 接入压缩输入，使压缩只消费已规范化的模型可见表示；
4. 在隔离 HOME 下运行 SQLite close/reopen replay smoke；
5. 通过所有测试后再进入后续压缩 transition change。

回滚时可恢复旧调用路径，但旧路径不应重新被标记为 prefix-cache-safe。若发现 Canonical 数据已使用多种表示，保留事件原文，使用 versioned materializer 进行受控读取，不直接改写历史事件。

## Open Questions

无。Provider 支持的具体 block 集合和失败策略属于本 change 的明确实现边界，不能延后到实施阶段临时决定。

## Post-audit corrections

Transition and compaction payloads carry the exact effective model-visible
representation used for replay. Secret detection remains active, while the
generic string-bounding pass does not rewrite digest-covered replay content;
otherwise a prepared event could become impossible to validate against its own
transition digest. When secret redaction does change a covered value, the
preparation boundary re-derives its replacement and transition digests from
the final persisted payload.
