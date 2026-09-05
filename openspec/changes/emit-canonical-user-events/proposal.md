## Why

Anthropic/OpenAI Agent Loop 只将用户消息追加到 provider message array，没有发射 canonical user event，导致 Session、Model Replay 和 Recovery 无法完整重建真实输入。

## What Changes

- 在真实 provider loop 的 turn boundary 发射 canonical user event。
- 区分原始用户输入、记忆注入和工具结果等系统上下文。
- 增加双 provider 的真实 fake-provider one-shot 事件链测试。

## Impact

影响 `agent.py`、event fixtures、projection/recovery tests；不改变 legacy message reader。
