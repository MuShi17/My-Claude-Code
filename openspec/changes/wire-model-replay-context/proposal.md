## Why

`ModelReplayProjection` 当前只是独立 helper，真实 provider loop 仍以 `_anthropic_messages`/`_openai_messages` 为上下文事实源，C08 的 canonical-first 集成未完成。

## What Changes

- 让下一轮 provider context 从 canonical projection 构造。
- legacy arrays 仅作为兼容输出或 parity comparator 输入。
- 增加清空 legacy array 后仍可重建 context 的集成测试。

## Impact

影响 `agent.py`、projection adapter、fake provider tests；需依赖 R-04 的 user event。
