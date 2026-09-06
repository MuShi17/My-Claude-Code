## Why

记忆召回目前只修改当前 Provider working messages，没有对应的 Canonical Event。下一轮 Replay 或进程恢复会丢失模型上一轮实际看到的记忆内容，并且宽泛异常处理可能把持久化失败误当成已消费。

## What Changes

- 增加独立、可重放的 memory/context event，而不是回写原始 user event。
- 保存实际注入文本、来源标识、内容 digest、注入顺序和幂等键。
- Anthropic/OpenAI 两条路径统一记录和 Replay memory injection。
- 只有 Canonical 持久化成功后才标记 recall 已消费。
- 为 Session、Trace 和 Model Replay 增加 memory injection 的明确标识。

## Capabilities

### New Capabilities

- `durable-memory-context`: 定义记忆召回作为模型上下文事件的持久化和重放语义。

### Modified Capabilities

无。

## Impact

- 影响 `RuntimeEvent` content contract、Agent memory prefetch consumer、Model Replay、Session/Trace projection；
- 不修改原始用户输入事件；
- 需要处理同一 memory injection 的幂等、失败重试和跨进程恢复。
