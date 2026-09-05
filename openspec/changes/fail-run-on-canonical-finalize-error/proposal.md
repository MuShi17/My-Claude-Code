## Why

Agent chat 的 terminal finalize 异常只打印后继续返回，Canonical Store 无法封存时仍可能把 Run 报告为成功，违反 C05/C06/C11 的 durability gate。

## What Changes

- 将 canonical append/seal/fsync/finalizer 故障转换为 controlled run failure。
- 统一 CLI 状态、异常诊断和保留数据行为。
- 增加故障注入测试。

## Impact

影响 `agent.py`、`run_lifecycle.py`、CLI 和 store fault tests；不覆盖原始 provider exception。
