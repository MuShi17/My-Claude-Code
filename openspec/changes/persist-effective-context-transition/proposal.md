## Why

当前 Tier 1-3 轻量压缩只修改 Provider working messages，下一轮 Canonical Replay 可能恢复原始结果。完整压缩虽已有 checkpoint/reset event，但 checkpoint 与激活 transition 不是一个原子提交，且上下文有效版本缺少统一的 epoch 语义。

## What Changes

- 将轻量裁剪、stale snip、microcompact 和完整压缩统一建模为持久化的 effective-context transition。
- 记录目标事件/消息组、替换内容、原因、策略版本、投影版本、source high-water、source digest 和 context epoch。
- 使 checkpoint 与激活 transition 原子提交，只有提交成功的上下文变更才能用于下一次 Provider 请求。
- Replay 按已提交 transition 重建有效上下文，不根据当前时间或当前 token 利用率重新裁剪旧事件。
- 保留 tool call/result 配对，避免压缩产生孤立工具结果。

## Capabilities

### New Capabilities

- `effective-context-transition`: 定义可重建、可验证、可原子激活的上下文压缩变更。

### Modified Capabilities

无。

## Impact

- 影响 Agent 压缩管线、checkpoint 写入和 Model Replay；
- 扩展 SQLite checkpoint/transition 事务边界；
- 增加 context epoch、transition digest 和故障恢复测试；
- 不删除 Canonical Event，不改变 Canonical authority。
