## Why

当主 Agent、子 Agent、重试、Ctrl+C、asyncio cancellation、预算终止和 provider abort 分别处理时，同一 run 可能没有终态、重复终态或在终止后继续写事件。需要统一 run lifecycle，使 recovery 和 projection 能区分真正完成、失败、取消、abort 与未封存状态。

## What Changes

- 为每个用户 turn 建立 main run，为子 Agent 建立独立 run identity 和 `parent_run_id`。
- 定义 open/running/terminal 状态机、合法终态集合、唯一终态和生命周期 guard。
- 将显式 retry 表达为可追踪 attempt/run 关系，禁止自动恢复不确定的工具副作用。
- 覆盖 provider error、tool error、budget、Ctrl+C、asyncio cancellation 和 abort 的封存路径。
- 不在本 change 引入“恢复后继续原 Run”的 continuation 语义。

## Capabilities

### New Capabilities

- `run-lifecycle`: 主/子 run、attempt、取消/失败/预算终止和唯一 terminal seal 的统一生命周期。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 影响 `src/mini_claude/agent.py`、`subagent.py`、session/CLI 生命周期、C05 store 的 seal API 和 C06 recorder。
- 为 C08 projection、C10 recovery 提供 parent/attempt/terminal 事实。
- 公开 Ctrl+C、权限和预算行为保持语义兼容；仅增加可恢复的记录和防止重复写。
