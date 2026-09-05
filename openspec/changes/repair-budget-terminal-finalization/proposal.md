## Why

Agent 在 model recorder 已经 `finish()` 后再次调用 `budget_exceeded()`，预算场景实际抛出 `model call has already finished`，导致 Run 缺少要求的 budget terminal evidence。

## What Changes

- 统一 model call 与 run terminal 的边界。
- 让 budget terminal、late event 和重复 seal 幂等且可诊断。
- 增加预算、取消和 provider error fault matrix。

## Impact

影响 `agent.py`、`runtime_lifecycle.py`、`run_lifecycle.py` 和生命周期测试。
