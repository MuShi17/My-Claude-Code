## Why

Canonical Runtime Event 已经能够保存 bounded tool result，但首次 Provider 请求、Canonical 持久化和 Replay 仍可能对同一个结构化结果采用不同的序列化规则。语义相同而 wire bytes 不同会破坏共享前缀缓存，并使 Provider-specific tool result 形状难以稳定验证。

## What Changes

- 增加统一的模型可见工具结果 materialization 规则。
- 对文本、普通 JSON 值、bounded artifact placeholder 和合法 content blocks 做明确分类。
- 让首次请求、Replay、压缩输入使用同一个确定性表示。
- 由 Anthropic/OpenAI adapter 分别生成合法的 Provider 消息结构，禁止 neutral dict 直接透传到 wire payload。
- 增加首次请求、SQLite 重开后 Replay、嵌套 JSON 和大结果 placeholder 的请求级字节一致性测试。

## Capabilities

### New Capabilities

- `provider-visible-tool-content`: 定义工具结果在 Canonical、Replay 和 Provider 请求之间的稳定、可验证表示。

### Modified Capabilities

无。

## Impact

- 影响 `src/mini_claude/agent.py` 的工具结果 materialization 路径；
- 影响 `src/mini_claude/projections/model_replay_projection.py` 和 `provider_context.py` 的工具结果投影与 Provider 适配；
- 复用现有 redaction、artifact archive 和 Runtime Event，不改变 Canonical Event 作为事实来源的原则；
- 增加 Agent integration tests 和 wire-level serialization assertions；
- 不改变已有合法文本、thinking/signature 或 tool-call 的对外语义。
