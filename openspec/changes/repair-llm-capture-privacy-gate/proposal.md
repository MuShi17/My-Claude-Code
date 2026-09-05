## Why

两份独立验收均确认 `LLMCapturePolicy(mode="off")` 仍会从 Agent 的 legacy shadow 路径写入完整 request/response，导致敏感内容落盘。该问题直接违反 C09 的隐私 Gate，必须在任何 Canonical authority 切换前修复。

## What Changes

- 使 capture policy 同时约束 canonical、legacy、shadow 和异常路径。
- `off` 只保留最小 metadata，不调用 raw legacy LLM writer。
- 增加 Agent 集成级 secret marker 测试。

## Impact

影响 `agent.py`、capture/logger 集成与测试；不迁移或删除历史 LLM 文件。
