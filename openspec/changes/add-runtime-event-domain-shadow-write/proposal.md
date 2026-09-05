## Why

C01 已冻结事件语义，但 Agent Loop 仍直接调用 legacy `log_*` 方法，provider 分支无法共享一个发射边界。需要先引入纯领域 RuntimeEvent 与可组合 sink，使事件校验、脱敏和 legacy shadow 行为可以独立于 SQLite 实现并被后续 Store 复用。

## What Changes

- 增加 provider-neutral RuntimeEvent domain model、RunContext、ID 生成和 canonical 编码。
- 增加统一 redaction、refs 和 bounded payload 处理。
- 定义 EventSink、canonical sink、legacy adapter、shadow/composite sink 及故障策略。
- 让调用方只依赖一个 emit/append facade，并保留 memory/recording sink 以支持离线测试。
- 明确 canonical sink 的失败不能静默，diagnostic legacy sink 的失败不改变执行语义。

## Capabilities

### New Capabilities

- `runtime-event-shadow-write`: RuntimeEvent 领域对象、发射门面、脱敏、canonical/legacy/shadow sink 组合和失败策略。

### Modified Capabilities

<!-- The frozen contract is consumed, not modified; no existing capability delta is needed. -->

## Impact

- 新增 `src/mini_claude/runtime_event.py`、`event_ids.py`、`redaction.py`、`event_sink.py`，并为 C05 的 SQLite sink 提供接口。
- 影响 logger/tracer 适配和后续 agent loop 接入，但本 change 不让 SQLite 成为执行路径事实源。
- 依赖 C01、C02、C03；必须在 C05 store 不变量通过前保持内存/记录型 sink。
