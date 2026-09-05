## Why

在 Canonical Store 接管前，旧日志仍然是用户可见的调试和兼容数据源；当前 child logger 可能没有 active ask、timestamp 固定为 `.000Z`、API response 缺少 `llm_ref`，Tracer 的 turn 会在工具详情写入前落盘，导致旧格式本身不完整。若不先修复，shadow parity 的差异无法归因。

## What Changes

- 修复 child Agent 的 ask/log 生命周期，使父子事件均能落到正确的 legacy session 文件。
- 使用真实 UTC 毫秒时间戳，并补齐 API response 与 LLM 文件的关联引用。
- 让 turn 持久化在工具细节、错误和最终状态齐全后仍保持可读一致。
- 保留现有 JSONL、traces、llm 文件布局和公开 Agent 行为，不删除或改写历史数据。
- 为 legacy 正确性建立回归测试，作为 C04 shadow sink 的兼容输入。

## Capabilities

### New Capabilities

- `legacy-agent-log-correctness`: 旧 AgentLogger/Tracer 输出的身份、时间、关联引用、工具细节和失败持久化保证。

### Modified Capabilities

<!-- No existing OpenSpec capability is modified. -->

## Impact

- 影响 `src/mini_claude/logger.py`、`tracer.py`、`agent.py` 以及相关 session/child-agent 调用点。
- 影响 `~/.mini-claude/logs`、`llm`、`traces` 的新写入格式，但不迁移旧文件。
- 依赖 C01 的边界与 C02 的离线 fixture；与 C04 的 canonical facade 依赖顺序为 legacy correctness 先行。
