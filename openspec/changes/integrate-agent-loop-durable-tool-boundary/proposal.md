## Why

当前 `agent.py` 的 Anthropic/OpenAI 分支分别记录 API、流式 delta、工具调用和结果，工具真正执行前没有统一的 durable dispatch 事实，进程崩溃可能留下无法判断是否执行过的 tool call。需要把 Agent Loop 接到 C04/C05 facade，并把模型生命周期和工具副作用边界统一起来。

## What Changes

- 以统一 ModelCallRecorder 表达双 provider 的 request/response/stream/usage/latency/finish/error 生命周期。
- 强制 function call、权限决定、durable tool dispatch、实际执行、tool outcome、function response 的顺序。
- 在流式工具参数未完成时只写 bounded partial，不允许提前执行工具。
- 对工具成功、失败、拒绝、超时、预算耗尽和 provider 错误发射可恢复事件。
- 移除 `AgentLogger.log_*` 作为 Agent Loop 事实源，但保留 C04 的 legacy shadow。

## Capabilities

### New Capabilities

- `agent-loop-durable-tool-boundary`: 双 provider 模型生命周期、durable tool dispatch、partial streaming 和统一错误/使用量记录。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 主要影响 `src/mini_claude/agent.py`、provider streaming helpers、tools/permissions、logger facade 和 C02 fixtures。
- 要求 C04 domain sink 与 C05 SQLite store 已通过其不变量测试。
- 公开工具权限语义保持兼容；Canonical event 成为事实输出，legacy 继续 shadow。
