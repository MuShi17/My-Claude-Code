## Why

Canonical events只有在能稳定投影为 session 消息、provider 请求和 trace 时才可替代现有分散日志。当前 session.json、Tracer 和 model messages 各自维护状态，无法从 immutable prefix 重建或验证高水位；需要建立不修改事实源的 projection/replay 层。

## What Changes

- 从 canonical event high-water 构建 SessionProjection、ModelReplayProjection 和 RunTraceProjection。
- 定义 partial、hidden/model visibility、thinking signature、tool call/result pairing 和 error 的投影规则。
- 支持 projection rebuild、stable digest、高水位增量和损坏/缺失事件诊断。
- 让 Agent Loop 的下一轮消息和 session 读取使用 canonical projection，不从 legacy logger 补事实。
- 保持 projection 为可重建派生物，不把 projection 写回 canonical event。

## Capabilities

### New Capabilities

- `runtime-projections`: Canonical event 到 session、provider model replay 和 diagnostic run trace 的确定性投影与重建。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 新增 `src/mini_claude/projections/session_projection.py`、`model_replay_projection.py`、`run_trace_projection.py`。
- 影响 `session.py`、Agent Loop context construction、Tracer compatibility 和 C05 high-water read API。
- 依赖 C01、C02、C04～C07；不允许反向修改冻结事件语义。
