## Why

Canonical event、SQLite、Agent Loop、生命周期、投影、归档和 resume 只有在端到端经过 shadow parity 和故障注入后才能成为 authority。若直接切换，provider 差异、旧数据兼容缺口或 canonical/legacy 双写失败可能在用户会话中暴露且难以回滚。

## What Changes

- 建立双 provider、权限、终态、子 Agent、存储故障、context/recovery、privacy 的固定 parity 场景。
- 比较 canonical projection 与 legacy 输出的稳定字段，区分允许差异和必须修复的 gap。
- 增加 strict OpenSpec/diff 检查、临时 HOME CLI smoke、故障注入和旧数据保护验收。
- 通过显式配置控制 shadow、canonical-first authority 和 rollback，切换前要求 blocker/gap closure 证据。
- 维持 runtime.sqlite、legacy JSONL/session/traces/llm 的可读性；不以删除用户数据完成切换。

## Capabilities

### New Capabilities

- `canonical-cutover-validation`: Canonical/legacy shadow parity、故障验收、authority 切换、回滚和 gap closure 的质量门禁。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 影响 `src/mini_claude` 的 feature flags/CLI、C02 测试 harness、projection/recovery/store diagnostics 和交付文档。
- 依赖 C01～C10 全部完成；这是任何 authority 切换或交付动作前的最终 Gate。
- 不执行 commit、push、merge、发布、删除旧数据或清理用户工作区。
