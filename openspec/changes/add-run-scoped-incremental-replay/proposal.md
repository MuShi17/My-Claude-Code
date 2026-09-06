## Why

Agent 当前在每个 Provider step 前读取并归约完整 Canonical Event Store，长任务的重复扫描和消息物化成本随历史长度与步骤数累积。Maka 复用稳定 prior prefix，仅投影当前 Turn 的新增 durable events，适合本项目的本地单 Run 边界。

## What Changes

- 在一次 Run 内增加带 high-water、source digest、projection version 和 context epoch 的 replay cursor。
- prior history 在 Run 开始时一次 materialize；后续只读取新增 ordinal 并生成 current-turn suffix。
- 增量路径复用 Canonical reducer，保留未完成 tool group、thinking、文本和多工具配对状态。
- checkpoint/transition、版本变化、digest 不一致或缓存损坏时受控 cold rebuild。
- 增加扫描范围、投影耗时、前缀摘要和 rebuild reason 诊断。

## Capabilities

### New Capabilities

- `run-scoped-incremental-replay`: 定义稳定历史前缀与当前 Turn 增量后缀的可验证重放协议。

### Modified Capabilities

无。

## Impact

- 影响 Agent provider loop、Model Replay source interface、SQLite read APIs 和 context diagnostics；
- 依赖前置的 provider-visible content 和 effective-context transition change；
- 不改变 Canonical Event authority，不允许增量缓存成为不可重建的第二事实源；
- 增加冷重建等价性、cursor invalidation 和读取计数测试。
