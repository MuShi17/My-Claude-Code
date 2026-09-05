## Why

C11 声称完成 one-shot/list/latest/resume 和 shadow parity，但现有 smoke 主要覆盖 help/resume；同时能力已经存在的 pending `xfail` 和旧测试数量硬编码仍未清理。

## What Changes

- 使用临时 HOME/fake provider 补齐真实 CLI 全链路证据。
- 清理 stale xfail 与硬编码历史测试基线。
- 将允许差异和 parity 结果写入测试矩阵。

## Impact

影响 CLI/test fixtures、`test_cutover_gate.py`、`test_cli_smoke.py`、测试矩阵和架构文档。
